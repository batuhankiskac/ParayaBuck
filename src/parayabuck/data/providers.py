"""Research data provider abstraction.

``ResearchDataProvider`` is the research-only data interface; the
:class:`VersionedResearchStore` stays a pure storage layer. Research must
never assume a provider's adjustment semantics: every provider declares its
``adjustment_capabilities`` and its ``metadata`` block, and datasets built
from a provider with an unknown adjustment policy are rejected by the dataset
quality gate.

Adaptors:

* :class:`AlpacaResearchDataProvider` — Alpaca market data (IEX or SIP feeds).
* :class:`LocalResearchDataProvider` — reads previously persisted datasets.
* :class:`ExternalAdjustedDailyProvider` — vendor-adjusted daily data (Stooq)
  used when the execution broker's feed cannot deliver long enough or
  dividend-adjusted history. Its correctness is never assumed: it must pass
  :func:`reconcile_providers` against Alpaca's ``all`` adjustment on the
  overlapping window before its dataset is accepted.
"""

from __future__ import annotations

import csv
import io
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from parayabuck.core.models import Bar, Market
from parayabuck.data.alpaca import AlpacaHistoricalData
from parayabuck.data.research_store import ProviderMetadata, VersionedResearchStore
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import weekday_count

# Stooq vendor symbols for the permanent ETF research universe.
STOOQ_SYMBOL_MAP: dict[str, str] = {
    "SPY": "spy.us",
    "QQQ": "qqq.us",
    "IWM": "iwm.us",
    "XLB": "xlb.us",
    "XLC": "xlc.us",
    "XLE": "xle.us",
    "XLF": "xlf.us",
    "XLI": "xli.us",
    "XLK": "xlk.us",
    "XLP": "xlp.us",
    "XLRE": "xlre.us",
    "XLU": "xlu.us",
    "XLV": "xlv.us",
    "XLY": "xly.us",
    "SHY": "shy.us",
    "BTC/USD": "btcusd",
}

STOOQ_DAILY_URL = "https://stooq.com/q/d/l/"


class ResearchDataProvider(ABC):
    """Research-only market data interface (no execution responsibility)."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        adjustment: str,
    ) -> list[Bar]: ...

    @abstractmethod
    def resolve_symbol(self, symbol: str, market: Market | None = None) -> str: ...

    @abstractmethod
    def adjustment_capabilities(self) -> tuple[str, ...]: ...

    @abstractmethod
    def metadata(self, symbol: str) -> ProviderMetadata: ...

    @abstractmethod
    def validate_coverage(
        self,
        symbol: str,
        bars: list[Bar],
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[bool, str]: ...


class AlpacaResearchDataProvider(ResearchDataProvider):
    """Alpaca market-data adapter with explicit feed and adjustment policy."""

    def __init__(
        self,
        history: AlpacaHistoricalData,
        *,
        feed: str,
        data_delayed: bool,
        research_differs_from_execution: bool,
        provider_version: str = "alpaca-data-sdk",
    ) -> None:
        self._history = history
        self._feed = feed
        self._data_delayed = data_delayed
        self._research_differs_from_execution = research_differs_from_execution
        self._provider_version = provider_version

    @property
    def provider_name(self) -> str:
        return "alpaca"

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        adjustment: str,
    ) -> list[Bar]:
        bars: list[Bar] = self._history.get_bars(
            symbol,
            timeframe,
            start=start,
            end=end,
            adjustment=adjustment,
        )
        return bars

    def resolve_symbol(self, symbol: str, market: Market | None = None) -> str:
        canonical: str = canonical_symbol(symbol, market)
        return canonical

    def adjustment_capabilities(self) -> tuple[str, ...]:
        return ("raw", "splits", "all")

    def metadata(self, symbol: str, adjustment: str = "all") -> ProviderMetadata:
        return ProviderMetadata(
            provider_name=self.provider_name,
            source_feed="sip" if not self._data_delayed else "delayed_sip",
            vendor_symbol=canonical_symbol(symbol),
            adjustment_method=adjustment,
            currency="USD",
            timezone="America/New_York",
            exchange_calendar="NYSE",
            downloaded_at=datetime.now(UTC),
            provider_version=self._provider_version,
            research_provider_differs_from_execution=self._research_differs_from_execution,
            notes=f"feed={self._feed}",
        )

    def validate_coverage(
        self,
        symbol: str,
        bars: list[Bar],
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[bool, str]:
        if not bars:
            return False, "no bars returned"
        ordered = sorted(bars, key=lambda bar: bar.timestamp)
        if ordered[0].timestamp > start + timedelta(days=30):
            # A late listing is legitimate. Reject only a late AND sparse
            # response here; the listing-aware dataset-quality gate remains
            # authoritative for full-window completeness.
            span_days = (ordered[-1].timestamp - ordered[0].timestamp).days + 1
            if span_days > 0 and len(ordered) < 0.5 * weekday_count(
                ordered[0].timestamp, ordered[-1].timestamp
            ):
                return False, f"coverage starts late: {ordered[0].timestamp.date()}"
        return True, "ok"


class LocalResearchDataProvider(ResearchDataProvider):
    """Reads persisted datasets from the research store."""

    def __init__(self, store: VersionedResearchStore, dataset_id: str) -> None:
        manifest = store.manifest(dataset_id)
        if manifest is None:
            raise ValueError(f"Unknown research dataset: {dataset_id}")
        self._store = store
        self._dataset_id = dataset_id
        self._manifest = manifest

    @property
    def provider_name(self) -> str:
        return "local"

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        adjustment: str,
    ) -> list[Bar]:
        loaded = self._store.load_dataset(
            self._dataset_id,
            symbols=(canonical_symbol(symbol),),
            start=start,
            end=end,
        )
        bars: list[Bar] = loaded.get(canonical_symbol(symbol), [])
        return bars

    def resolve_symbol(self, symbol: str, market: Market | None = None) -> str:
        canonical: str = canonical_symbol(symbol, market)
        return canonical

    def adjustment_capabilities(self) -> tuple[str, ...]:
        return (self._manifest.adjustment,)

    def metadata(self, symbol: str) -> ProviderMetadata:
        if self._manifest.provider_metadata is not None:
            return self._manifest.provider_metadata
        return ProviderMetadata(
            provider_name=self.provider_name,
            source_feed=self._manifest.source_feed,
            vendor_symbol=canonical_symbol(symbol),
            adjustment_method=self._manifest.adjustment,
            currency="USD",
            timezone="America/New_York",
            exchange_calendar="NYSE",
            downloaded_at=self._manifest.downloaded_at,
            provider_version=self._manifest.universe_version,
            research_provider_differs_from_execution=True,
            notes="dataset persisted without explicit provider metadata",
        )

    def validate_coverage(
        self,
        symbol: str,
        bars: list[Bar],
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[bool, str]:
        if not bars:
            return False, "no bars in dataset partition"
        return True, "ok"


class ExternalAdjustedDailyProvider(ResearchDataProvider):
    """Vendor-adjusted daily equity data (Stooq) with explicit reconciliation.

    Correctness of the vendor's adjustment is never assumed: a dataset built
    from this provider must pass :func:`reconcile_providers` against Alpaca
    ``all``-adjusted bars on the overlapping window before acceptance, and
    the reconciliation report is recorded next to the dataset.

    NOTE (access status, 2026-08): stooq.com serves its download endpoint
    behind a JavaScript proof-of-work page; automated requests receive
    ``Access denied`` / ``404``. This adapter is therefore NOT usable for
    fresh downloads and is kept only as the reconciliation-target interface
    for vendor data. The equity research pipeline uses Alpaca SIP+IEX
    instead. Re-enable only after verifying the vendor's download endpoint
    is accessible to the research environment.
    """

    def __init__(
        self,
        *,
        symbol_map: dict[str, str] | None = None,
        research_differs_from_execution: bool = True,
        provider_version: str = "stooq-daily-v1",
        raw_fetcher: Callable[[str, datetime, datetime], str] | None = None,
    ) -> None:
        self._symbol_map = dict(symbol_map or STOOQ_SYMBOL_MAP)
        self._research_differs_from_execution = research_differs_from_execution
        self._provider_version = provider_version
        self._raw_fetcher = raw_fetcher or self._fetch_url

    @property
    def provider_name(self) -> str:
        return "stooq"

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        adjustment: str,
    ) -> list[Bar]:
        if timeframe != "1d":
            raise ValueError("ExternalAdjustedDailyProvider only serves 1d bars")
        if adjustment not in self.adjustment_capabilities():
            raise ValueError(f"Provider does not support adjustment {adjustment!r}")
        vendor = self._symbol_map.get(canonical_symbol(symbol))
        if vendor is None:
            raise ValueError(f"No vendor symbol mapping for {symbol}")
        raw = self._raw_fetcher(vendor, start, end)
        return self._parse_csv(raw, symbol, start, end)

    def resolve_symbol(self, symbol: str, market: Market | None = None) -> str:
        canonical: str = canonical_symbol(symbol, market)
        return canonical

    def adjustment_capabilities(self) -> tuple[str, ...]:
        return ("adjusted",)

    def metadata(self, symbol: str) -> ProviderMetadata:
        return ProviderMetadata(
            provider_name=self.provider_name,
            source_feed="stooq-daily",
            vendor_symbol=self._symbol_map.get(canonical_symbol(symbol), canonical_symbol(symbol)),
            adjustment_method="adjusted",
            currency="USD",
            timezone="America/New_York",
            exchange_calendar="NYSE",
            downloaded_at=datetime.now(UTC),
            provider_version=self._provider_version,
            research_provider_differs_from_execution=self._research_differs_from_execution,
            notes=(
                "vendor-adjusted daily close/OHLC; correctness verified by "
                "reconciliation against Alpaca 'all' on the overlap window"
            ),
        )

    def validate_coverage(
        self,
        symbol: str,
        bars: list[Bar],
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[bool, str]:
        if not bars:
            return False, "no bars returned"
        ordered = sorted(bars, key=lambda bar: bar.timestamp)
        if ordered[0].timestamp > start + timedelta(days=30):
            return False, f"coverage starts late: {ordered[0].timestamp.date()}"
        return True, "ok"

    @staticmethod
    def _fetch_url(vendor: str, start: datetime, end: datetime) -> str:
        import requests

        response = requests.get(
            STOOQ_DAILY_URL,
            params={
                "s": vendor,
                "i": "d",
                "d1": start.date().isoformat(),
                "d2": end.date().isoformat(),
            },
            timeout=60,
        )
        response.raise_for_status()
        return str(response.text)

    def _parse_csv(self, raw: str, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        market = Market.EQUITY
        bars: list[Bar] = []
        reader = csv.reader(io.StringIO(raw))
        header = next(reader, [])
        if not header or header[0].lower() != "date":
            raise ValueError(f"Unexpected Stooq CSV header: {header}")
        for row in reader:
            if len(row) < 6:
                continue
            try:
                timestamp = datetime.fromisoformat(row[0]).replace(tzinfo=None)
            except ValueError:
                timestamp = datetime.strptime(row[0], "%Y-%m-%d")
            timestamp = timestamp.replace(tzinfo=UTC)
            if not start <= timestamp <= end:
                continue
            try:
                open_price, high = float(row[1]), float(row[2])
                low, close = float(row[3]), float(row[4])
                volume = float(row[5])
            except ValueError:
                continue
            if close <= 0:
                continue
            bars.append(
                Bar(
                    timestamp=timestamp,
                    symbol=canonical_symbol(symbol, market),
                    timeframe="1d",
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    market=market,
                )
            )
        return bars


@dataclass(frozen=True)
class ProviderReconciliationReport:
    provider_a: str
    provider_b: str
    symbol: str
    overlap_start: datetime | None
    overlap_end: datetime | None
    overlapping_days: int
    mean_abs_return_diff: float
    max_abs_return_diff: float
    # date, return_a, return_b, likely_corporate_action
    abnormal_days: tuple[tuple[str, float, float, bool], ...]
    passed: bool
    maximum_tolerated_abs_return_diff: float = 0.005

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_a": self.provider_a,
            "provider_b": self.provider_b,
            "symbol": self.symbol,
            "overlap_start": self.overlap_start.isoformat() if self.overlap_start else None,
            "overlap_end": self.overlap_end.isoformat() if self.overlap_end else None,
            "overlapping_days": self.overlapping_days,
            "mean_abs_return_diff": self.mean_abs_return_diff,
            "max_abs_return_diff": self.max_abs_return_diff,
            "abnormal_days": [
                {"date": day, "return_a": ra, "return_b": rb, "likely_corporate_action": flag}
                for day, ra, rb, flag in self.abnormal_days
            ],
            "passed": self.passed,
            "maximum_tolerated_abs_return_diff": self.maximum_tolerated_abs_return_diff,
        }


def _daily_returns(bars: list[Bar]) -> dict[date, float]:
    """Daily return series keyed by trading date (robust to intraday
    timestamp conventions differing between feeds)."""
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    returns: dict[date, float] = {}
    for previous, current in zip(ordered, ordered[1:]):
        if previous.close > 0:
            returns[current.timestamp.date()] = current.close / previous.close - 1
    return returns


def reconcile_providers(
    symbol: str,
    primary_bars: list[Bar],
    reference_bars: list[Bar],
    *,
    provider_a: str,
    provider_b: str,
    maximum_tolerated_abs_return_diff: float = 0.005,
) -> ProviderReconciliationReport:
    """Compare daily return series of two providers over their overlap.

    The comparison is return-based (price levels differ by adjustment policy,
    returns must not). Days where one side moves while the other stays flat
    are flagged as likely corporate-action days and reported separately.
    """
    primary = _daily_returns(primary_bars)
    reference = _daily_returns(reference_bars)
    common = sorted(set(primary) & set(reference))
    if not common:
        return ProviderReconciliationReport(
            provider_a,
            provider_b,
            symbol,
            None,
            None,
            0,
            0.0,
            0.0,
            (),
            False,
            maximum_tolerated_abs_return_diff,
        )
    diffs = [abs(primary[day] - reference[day]) for day in common]
    abnormal: list[tuple[str, float, float, bool]] = []
    for day in common:
        diff = abs(primary[day] - reference[day])
        if diff > maximum_tolerated_abs_return_diff:
            flat_side = min(abs(primary[day]), abs(reference[day])) < 0.0005
            abnormal.append((day.isoformat(), primary[day], reference[day], flat_side))
    abnormal.sort(key=lambda item: item[1:], reverse=True)
    # A day is tolerated only when one side is flat (a corporate action the
    # other feed already adjusted); an unexplained move of both sides fails.
    passed = len(common) >= 20 and all(flag for _day, _ra, _rb, flag in abnormal)
    return ProviderReconciliationReport(
        provider_a,
        provider_b,
        symbol,
        datetime.combine(common[0], datetime.min.time(), tzinfo=UTC),
        datetime.combine(common[-1], datetime.min.time(), tzinfo=UTC),
        len(common),
        sum(diffs) / len(diffs),
        max(diffs),
        tuple(abnormal),
        passed,
        maximum_tolerated_abs_return_diff,
    )


__all__ = [
    "AlpacaResearchDataProvider",
    "ExternalAdjustedDailyProvider",
    "LocalResearchDataProvider",
    "ProviderReconciliationReport",
    "ResearchDataProvider",
    "STOOQ_DAILY_URL",
    "STOOQ_SYMBOL_MAP",
    "reconcile_providers",
]
