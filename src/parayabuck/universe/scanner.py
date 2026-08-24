from __future__ import annotations

import hashlib
import json
import logging
import math
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from parayabuck.config import EquityUniverseConfig
from parayabuck.core.models import Bar, Market
from parayabuck.features.indicators import true_ranges
from parayabuck.markets.sectors import is_leveraged_or_inverse_equity, sector_of
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.universe.history import append_snapshot_history
from parayabuck.utils.io import atomic_write


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


def equity_candidate_from_bars(
    symbol: str,
    bars: Sequence[Bar],
    *,
    expected_bar_count: int,
    economic_sector: str | None = None,
    exchange: str = "NASDAQ",
    tradable: bool = True,
    status: str = "active",
) -> EquityCandidate:
    """Build production scanner metrics from a point-in-time daily-bar window."""
    valid: list[Bar] = []
    for bar in bars:
        try:
            values = tuple(
                float(getattr(bar, field)) for field in ("high", "low", "close", "volume")
            )
        except (AttributeError, TypeError, ValueError):
            continue
        if all(math.isfinite(value) for value in values) and values[2] > 0 and values[3] >= 0:
            valid.append(bar)
    ordered = sorted(valid, key=lambda bar: getattr(bar, "timestamp"))[-20:]
    classified = is_leveraged_or_inverse_equity(symbol)
    if not ordered:
        return EquityCandidate(
            symbol,
            0,
            0,
            0,
            0,
            economic_sector=economic_sector,
            exchange=exchange,
            tradable=tradable,
            status=status,
            leveraged_or_inverse=classified,
        )
    price = float(getattr(ordered[-1], "close"))
    dollar_volumes = [
        float(getattr(bar, "close")) * float(getattr(bar, "volume")) for bar in ordered
    ]
    ranges = true_ranges(ordered)
    return EquityCandidate(
        symbol=symbol,
        price=price,
        median_dollar_volume_20d=statistics.median(dollar_volumes),
        atr_pct=statistics.mean(ranges) / price if ranges and price > 0 else 0.0,
        data_completeness=min(1.0, len(ordered) / max(1, expected_bar_count)),
        economic_sector=economic_sector,
        exchange=exchange,
        tradable=tradable,
        status=status,
        leveraged_or_inverse=classified,
    )


@dataclass(frozen=True)
class ScannerResult:
    selection_timestamp: str
    data_timestamp: str
    # The ELIGIBLE POOL (tradability-first): reserved + every candidate that
    # passes the scanner's eligibility filters. This is the alpha-search
    # universe — deliberately larger than the streamed observation set.
    selected_symbols: tuple[str, ...]
    candidate_symbols: tuple[str, ...]
    score_breakdown: dict[str, dict[str, float | str]]
    eliminated: dict[str, str]
    scanner_version: str
    config_digest: str
    sector_distribution: dict[str, int]
    status: str = "success"
    sector_metadata_coverage: float = 1.0
    # The STREAMED observation set: at most ``maximum_stream_symbols``.
    # Defaults to the scanner's eligibility-quality ranking; the strategy
    # alpha ranking replaces it when a candidate ranker is configured.
    observation_set: tuple[str, ...] = ()
    observation_eliminated: dict[str, str] = field(default_factory=dict)
    observation_strategy_name: str = ""
    observation_strategy_version: str = ""
    observation_strategy_timeframe: str | None = None


class EquityUniverseScanner:
    """Deterministic pre-session selector; it never opens a market-data stream."""

    version = "equity-scanner-v3"

    def __init__(self, config: EquityUniverseConfig) -> None:
        self.config = config
        self.scanner = config.scanner

    def scan(
        self,
        candidates: list[EquityCandidate],
        *,
        data_timestamp: datetime,
        selection_timestamp: datetime | None = None,
        pinned_symbols: tuple[str, ...] = (),
    ) -> ScannerResult:
        """Deterministically select the equity stream universe.

        Preconditions: candidates must be canonical, timestamps must be
        timezone-aware, and pool membership (candidate file) is the caller's
        responsibility. Reserved symbols (references, approved inverse ETFs,
        runtime pins) are force-kept even when they fail a filter; pool
        membership is enforced by ``validate_active_universe`` at load time.
        """
        references = self.config.reference_symbols
        if data_timestamp.tzinfo is None or (
            selection_timestamp is not None and selection_timestamp.tzinfo is None
        ):
            raise ValueError("Scanner timestamps must be timezone-aware (UTC)")
        pinned = tuple(
            symbol
            for symbol in dict.fromkeys(
                canonical_symbol(item, Market.EQUITY) for item in pinned_symbols
            )
            if symbol not in references and symbol not in self.config.inverse_trade_symbols
        )
        reserved = tuple(dict.fromkeys((*references, *self.config.inverse_trade_symbols, *pinned)))
        if len(reserved) > self.config.maximum_stream_symbols:
            raise ValueError("Reference and pinned symbols exceed the equity stream limit")
        eliminated: dict[str, str] = {}
        scored: list[tuple[float, str, str, dict[str, float | str]]] = []
        seen: set[str] = set()
        candidate_sector: dict[str, str] = {}
        sector_known = 0
        for candidate in candidates:
            symbol = canonical_symbol(candidate.symbol, Market.EQUITY)
            if symbol in seen:
                raise ValueError(f"Duplicate canonical scanner candidate: {symbol}")
            seen.add(symbol)
            reason = self._filter_reason(candidate)
            if reason:
                # Reserved symbols are force-kept (they are streamed
                # regardless): reporting them as eliminated while they remain
                # selected is contradictory output. Mirror the crypto scanner.
                if symbol not in reserved:
                    eliminated[symbol] = reason
                continue
            broker_sector = (candidate.economic_sector or "").strip().lower()
            static_sector = sector_of(symbol).strip().lower()
            sector = broker_sector or (static_sector if static_sector != "other" else "unknown")
            candidate_sector[symbol] = sector
            if broker_sector or static_sector != "other":
                sector_known += 1
            liquidity = min(1.0, math.log10(candidate.median_dollar_volume_20d) / 10)
            volatility_fit = max(
                0.0,
                1
                - abs(candidate.atr_pct - self.scanner.volatility_optimum_pct)
                / max(0.001, self.scanner.maximum_atr_pct - self.scanner.minimum_atr_pct),
            )
            spread_quality = (
                0.5
                if candidate.spread_bps is None
                else max(0.0, 1 - candidate.spread_bps / self.scanner.spread_quality_ceiling_bps)
            )
            score = (
                liquidity * 40
                + volatility_fit * 25
                + candidate.data_completeness * 25
                + spread_quality * 10
                - (self.scanner.unknown_sector_penalty if sector == "unknown" else 0.0)
            )
            breakdown: dict[str, float | str] = {
                "total": round(score, 8),
                "liquidity": round(liquidity * 40, 8),
                "volatility_fit": round(volatility_fit * 25, 8),
                "data_completeness": round(candidate.data_completeness * 25, 8),
                "spread_quality": round(spread_quality * 10, 8),
                "economic_sector": sector,
                "sector_source": (
                    "candidate_metadata"
                    if broker_sector
                    else ("explicit_static_map" if static_sector != "other" else "unavailable")
                ),
            }
            scored.append((score, symbol, sector, breakdown))

        # ELIGIBILITY-FIRST POOL: reserved + every candidate that passes the
        # tradability filters. No stream-capacity, issuer or sector
        # truncation here — the alpha strategy ranks THIS pool, and only then
        # is the streamed observation set truncated (stream limit !=
        # opportunity universe).
        pool = list(reserved)
        breakdowns: dict[str, dict[str, float | str]] = {
            symbol: {
                "total": 0.0,
                "selection_role": (
                    "reference"
                    if symbol in references
                    else (
                        "inverse_hedge"
                        if symbol in self.config.inverse_trade_symbols
                        else "pinned_managed_position"
                    )
                ),
                "economic_sector": candidate_sector.get(symbol, sector_of(symbol)),
            }
            for symbol in reserved
        }
        sector_counts: dict[str, int] = {}
        for symbol in reserved:
            sector = str(breakdowns[symbol]["economic_sector"]).lower()
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
        ordered_scored = sorted(scored, key=lambda item: (-item[0], item[1]))
        for _score, symbol, sector, breakdown in ordered_scored:
            if symbol in pool:
                continue
            pool.append(symbol)
            sector_counts[sector] = sector_counts.get(sector, 0) + 1
            breakdowns[symbol] = breakdown

        # FALLBACK OBSERVATION SET: the scanner's eligibility-quality top-N
        # with issuer/sector diversity (the pre-alpha stream behavior). A
        # strategy alpha ranking REPLACES this when one is configured; this
        # stays as the fail-safe stream set when no ranker exists.
        capacity = self.config.maximum_stream_symbols
        observation: list[str] = list(reserved)
        observation_eliminated: dict[str, str] = {}
        observation_sectors: dict[str, int] = {}
        observation_issuers: set[str] = set()
        for symbol in reserved:
            sector = str(breakdowns[symbol]["economic_sector"]).lower()
            observation_sectors[sector] = observation_sectors.get(sector, 0) + 1
            observation_issuers.add(_issuer_key(symbol))
        for _score, symbol, sector, _breakdown in ordered_scored:
            if symbol in observation:
                continue
            issuer = _issuer_key(symbol)
            if issuer in observation_issuers:
                observation_eliminated[symbol] = "duplicate_issuer"
                continue
            if len(observation) >= capacity:
                observation_eliminated.setdefault(symbol, "stream_capacity")
                continue
            sector_limit = capacity if sector == "unknown" else self.scanner.maximum_per_sector
            if observation_sectors.get(sector, 0) >= sector_limit:
                observation_eliminated[symbol] = "sector_concentration"
                continue
            observation.append(symbol)
            observation_issuers.add(issuer)
            observation_sectors[sector] = observation_sectors.get(sector, 0) + 1
        timestamp = (selection_timestamp or datetime.now(UTC)).astimezone(UTC)
        coverage = sector_known / len(scored) if scored else 1.0
        return ScannerResult(
            selection_timestamp=timestamp.isoformat(),
            data_timestamp=data_timestamp.astimezone(UTC).isoformat(),
            selected_symbols=tuple(pool),
            candidate_symbols=tuple(sorted(seen)),
            score_breakdown=breakdowns,
            eliminated=dict(sorted(eliminated.items())),
            scanner_version=self.version,
            config_digest=self._config_digest(),
            sector_distribution=dict(sorted(sector_counts.items())),
            sector_metadata_coverage=round(coverage, 6),
            observation_set=tuple(observation),
            observation_eliminated=dict(sorted(observation_eliminated.items())),
        )

    def persist(self, result: ScannerResult) -> None:
        payload = asdict(result)
        atomic_write(
            Path(self.config.active_universe_path),
            json.dumps(payload, indent=2, sort_keys=True),
        )
        append_snapshot_history(self.config.active_universe_path, payload)

    def _filter_reason(self, item: EquityCandidate) -> str | None:
        if not item.tradable or item.status.lower() != "active":
            return "not_active_tradable"
        if item.exchange.upper() in {"OTC", "OTCQX", "OTCQB", "PINK"}:
            return "otc"
        if (
            item.leveraged_or_inverse
            and canonical_symbol(item.symbol) not in self.config.inverse_trade_symbols
        ):
            return "leveraged_or_inverse"
        if not math.isfinite(item.price) or item.price < self.scanner.minimum_price:
            return "minimum_price"
        if (
            not math.isfinite(item.median_dollar_volume_20d)
            or item.median_dollar_volume_20d < self.scanner.minimum_median_dollar_volume
        ):
            return "minimum_liquidity"
        if not math.isfinite(item.atr_pct) or not (
            self.scanner.minimum_atr_pct <= item.atr_pct <= self.scanner.maximum_atr_pct
        ):
            return "atr_range"
        if (
            not math.isfinite(item.data_completeness)
            or not 0 <= item.data_completeness <= 1
            or item.data_completeness < self.scanner.minimum_data_completeness
        ):
            return "data_completeness"
        if item.spread_bps is not None and (
            not math.isfinite(item.spread_bps) or item.spread_bps < 0
        ):
            return "invalid_spread"
        return None

    def _config_digest(self) -> str:
        return scanner_config_digest(self.config)


def _issuer_key(symbol: str) -> str:
    """Collapse economically identical share classes for concentration checks."""
    return "ALPHABET" if symbol in {"GOOG", "GOOGL"} else symbol


def scanner_config_digest(config: EquityUniverseConfig) -> str:
    payload_dict = asdict(config)
    for key in (
        "observation_strategy_name",
        "observation_strategy_version",
        "observation_strategy_timeframe",
    ):
        payload_dict.pop(key, None)
    payload = json.dumps(payload_dict, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def validate_active_universe(config: EquityUniverseConfig) -> tuple[tuple[str, ...], str]:
    path = Path(config.active_universe_path)
    if not path.exists():
        return (), "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "success":
            return (), "invalid"
        if payload.get("scanner_version") != EquityUniverseScanner.version:
            return (), "invalid"
        if config.require_scanner_config_digest_match and payload.get(
            "config_digest"
        ) != scanner_config_digest(config):
            return (), "digest_mismatch"
        expected_strategy_name = config.observation_strategy_name
        if expected_strategy_name and (
            payload.get("observation_strategy_name") != expected_strategy_name
            or payload.get("observation_strategy_version") != config.observation_strategy_version
            or payload.get("observation_strategy_timeframe")
            != config.observation_strategy_timeframe
        ):
            return (), "observation_strategy_mismatch"
        selection_at = datetime.fromisoformat(str(payload["selection_timestamp"]))
        data_at = datetime.fromisoformat(str(payload["data_timestamp"]))
        if selection_at.tzinfo is None or data_at.tzinfo is None:
            return (), "invalid"
        now = datetime.now(UTC)
        selection_age_hours = (now - selection_at.astimezone(UTC)).total_seconds() / 3600
        data_age_hours = (now - data_at.astimezone(UTC)).total_seconds() / 3600
        if (
            selection_age_hours < 0
            or data_age_hours < 0
            or selection_age_hours > config.active_universe_max_age_hours
            or data_age_hours > config.active_universe_max_age_hours
        ):
            return (), "stale"
        raw = payload["selected_symbols"]
        if not isinstance(raw, list):
            return (), "invalid"
        selected = tuple(canonical_symbol(str(item), Market.EQUITY) for item in raw)
        if len(selected) != len(set(selected)):
            return (), "invalid"
        raw_candidates = payload["candidate_symbols"]
        if not isinstance(raw_candidates, list):
            return (), "invalid"
        candidates = {canonical_symbol(str(item), Market.EQUITY) for item in raw_candidates}
        breakdown = payload["score_breakdown"]
        if not isinstance(breakdown, dict) or not set(selected).issubset(breakdown):
            return (), "invalid"
        managed_pins = {
            symbol
            for symbol in selected
            if isinstance(breakdown.get(symbol), dict)
            and breakdown[symbol].get("selection_role") == "pinned_managed_position"
        }
        allowed = {
            *candidates,
            *config.reference_symbols,
            *config.inverse_trade_symbols,
            *managed_pins,
        }
        if not set(selected).issubset(allowed):
            return (), "outside_candidate_pool"
        required = {*config.reference_symbols, *config.inverse_trade_symbols}
        if not required.issubset(selected):
            return (), "invalid"
        # The eligible pool may exceed the stream capacity; the OBSERVATION
        # SET is the streamed subset and must respect the cap.
        raw_observation = payload.get("observation_set")
        if not isinstance(raw_observation, list):
            return (), "invalid"
        observation = tuple(canonical_symbol(str(item), Market.EQUITY) for item in raw_observation)
        if len(observation) != len(set(observation)):
            return (), "invalid"
        if not set(observation).issubset(set(selected)):
            return (), "observation_outside_pool"
        if len(observation) > config.maximum_stream_symbols:
            return (), "invalid"
        if not required.issubset(observation):
            return (), "invalid"
        return observation, "success"
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        # Fail closed, but surface the parse error: a persistent "invalid"
        # status is indistinguishable from rot without the underlying cause.
        logging.getLogger("parayabuck.universe").warning(
            "Active equity universe validation failed: %s", exc
        )
        return (), "invalid"


def load_active_symbols(
    config: EquityUniverseConfig,
    *,
    pinned_symbols: tuple[str, ...] = (),
    log_rejection: bool = True,
) -> tuple[str, ...]:
    """Load the streamed OBSERVATION SET (alpha-ranked top of the eligible
    pool) or return the configured fallback.

    ``validate_active_universe`` returns the validated observation set; the
    scanner's eligible pool stays the alpha-search universe and is NOT
    streamed wholesale.
    """
    references = config.reference_symbols
    selected: tuple[str, ...] = ()
    if config.dynamic_selection_enabled:
        selected, status = validate_active_universe(config)
        if status != "success" and log_rejection:
            logging.getLogger("parayabuck.universe").warning(
                "Active equity universe rejected; status=%s fallback=configured", status
            )
    trade = selected or config.fallback_trade_symbols
    combined = tuple(
        dict.fromkeys((*references, *trade, *(canonical_symbol(item) for item in pinned_symbols)))
    )
    if len(combined) > config.maximum_stream_symbols:
        kept = combined[: config.maximum_stream_symbols]
        logging.getLogger("parayabuck.universe").warning(
            "Equity stream capacity exceeded by references/pins; dropped_trade_symbols=%s",
            ",".join(combined[config.maximum_stream_symbols :]),
        )
        combined = kept
    return combined
