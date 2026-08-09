from __future__ import annotations

import hashlib
import json
import logging
import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from parayabuck.config import CryptoUniverseConfig
from parayabuck.core.models import Market
from parayabuck.markets.symbols import (
    canonical_symbol,
    is_stablecoin_pair,
    split_crypto_pair,
)
from parayabuck.utils.io import atomic_write

CRYPTO_CATEGORIES = {
    "BTC/USD": "core",
    "ETH/USD": "core",
    "SOL/USD": "layer1",
    "XRP/USD": "layer1",
    "ADA/USD": "layer1",
    "AVAX/USD": "layer1",
    "DOT/USD": "layer1",
    "LINK/USD": "defi",
    "UNI/USD": "defi",
    "AAVE/USD": "defi",
    "ONDO/USD": "defi",
    "FIL/USD": "infrastructure",
    "RENDER/USD": "infrastructure",
    "LTC/USD": "legacy",
    "BCH/USD": "legacy",
    "ARB/USD": "high_beta",
    "HYPE/USD": "high_beta",
    "DOGE/USD": "meme",
}


def _bar_close_volume(bar: object) -> tuple[float, float] | None:
    """Extract close/volume as finite floats, or None for malformed bars.

    The float conversion itself can raise (e.g. ``close=None`` from a partial
    API response); one bad bar must be skipped, not crash the whole refresh.
    """
    try:
        close = float(getattr(bar, "close", math.nan))
        volume = float(getattr(bar, "volume", math.nan))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(close) or not math.isfinite(volume):
        return None
    return close, volume


def crypto_candidate_from_bars(
    symbol: str,
    bars: Sequence[object],
    *,
    expected_bar_count: int,
    tradable: bool,
    status: str,
    btc_bars: Sequence[object] | None = None,
    spread_bps: float | None = None,
) -> CryptoCandidate:
    """Calculate scanner metrics without network or DataFrame dependencies.

    Alpaca crypto bar volume reflects activity visible on its own feed and can
    contain many zero-volume hours. Liquidity is therefore measured as median
    UTC daily notional instead of median hourly notional.
    """
    canonical = canonical_symbol(symbol, Market.CRYPTO)
    by_timestamp: dict[object, object] = {}
    for bar in bars:
        close_volume = _bar_close_volume(bar)
        if close_volume is None:
            continue
        close, volume = close_volume
        if close > 0 and volume >= 0 and hasattr(bar, "timestamp"):
            by_timestamp[getattr(bar, "timestamp")] = bar
    ordered = [
        by_timestamp[key]
        for key in sorted(
            by_timestamp,
            key=lambda item: item.isoformat() if isinstance(item, datetime) else str(item),
        )
    ]
    completeness = min(1.0, len(ordered) / max(1, expected_bar_count))
    daily_dollar_volumes: dict[object, float] = {}
    for bar in ordered:
        timestamp = getattr(bar, "timestamp")
        day = timestamp.astimezone(UTC).date() if isinstance(timestamp, datetime) else timestamp
        daily_dollar_volumes[day] = daily_dollar_volumes.get(day, 0.0) + (
            float(getattr(bar, "close")) * float(getattr(bar, "volume"))
        )
    true_ranges: list[float] = []
    for previous, current in zip(ordered, ordered[1:], strict=False):
        high = float(getattr(current, "high"))
        low = float(getattr(current, "low"))
        previous_close = float(getattr(previous, "close"))
        if all(math.isfinite(value) for value in (high, low, previous_close)):
            true_ranges.append(
                max(high - low, abs(high - previous_close), abs(low - previous_close))
            )
    price = float(getattr(ordered[-1], "close")) if ordered else 0.0
    atr_window = true_ranges[-14:]
    atr_pct = statistics.mean(atr_window) / price if atr_window and price > 0 else 0.0
    correlation = None
    if canonical != "BTC/USD" and btc_bars:
        correlation = _return_correlation(ordered, btc_bars)
    return CryptoCandidate(
        symbol=canonical,
        median_dollar_volume=(
            statistics.median(daily_dollar_volumes.values()) if daily_dollar_volumes else 0.0
        ),
        atr_pct=atr_pct,
        data_completeness=completeness,
        btc_correlation=correlation,
        category=CRYPTO_CATEGORIES.get(canonical, "unknown"),
        tradable=tradable,
        status=status,
        spread_bps=spread_bps,
        price=price,
        history_count=len(ordered),
    )


def _return_correlation(left_bars: Sequence[object], right_bars: Sequence[object]) -> float | None:
    def returns(items: Sequence[object]) -> dict[datetime, float]:
        ordered = sorted(items, key=lambda item: getattr(item, "timestamp"))
        result: dict[datetime, float] = {}
        for previous, current in zip(ordered, ordered[1:], strict=False):
            previous_close = float(getattr(previous, "close"))
            current_close = float(getattr(current, "close"))
            timestamp = getattr(current, "timestamp")
            if previous_close > 0 and current_close > 0 and isinstance(timestamp, datetime):
                result[timestamp] = math.log(current_close / previous_close)
        return result

    left = returns(left_bars)
    right = returns(right_bars)
    common = sorted(set(left) & set(right))
    if len(common) < 24:
        return None
    left_values = [left[key] for key in common]
    right_values = [right[key] for key in common]
    left_mean = statistics.mean(left_values)
    right_mean = statistics.mean(right_values)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left_values, right_values, strict=True)
    )
    left_scale = math.sqrt(sum((value - left_mean) ** 2 for value in left_values))
    right_scale = math.sqrt(sum((value - right_mean) ** 2 for value in right_values))
    denominator = left_scale * right_scale
    return max(-1.0, min(1.0, numerator / denominator)) if denominator > 0 else None


@dataclass(frozen=True)
class CryptoCandidate:
    symbol: str
    median_dollar_volume: float
    atr_pct: float
    data_completeness: float
    btc_correlation: float | None
    category: str = "unknown"
    tradable: bool = True
    status: str = "active"
    spread_bps: float | None = None
    price: float = 1.0
    history_count: int = 1


@dataclass(frozen=True)
class CryptoScannerResult:
    selection_timestamp: str
    data_timestamp: str
    selected_symbols: tuple[str, ...]
    candidate_symbols: tuple[str, ...]
    score_breakdown: dict[str, dict[str, float | str]]
    eliminated: dict[str, str]
    category_distribution: dict[str, int]
    scanner_version: str
    config_digest: str
    status: str = "success"


class CryptoUniverseScanner:
    """Deterministic, read-only crypto candidate selector."""

    version = "crypto-scanner-v1"

    def __init__(self, config: CryptoUniverseConfig) -> None:
        self.config = config

    def scan(
        self,
        candidates: list[CryptoCandidate],
        *,
        data_timestamp: datetime,
        selection_timestamp: datetime | None = None,
        pinned_symbols: tuple[str, ...] = (),
    ) -> CryptoScannerResult:
        """Deterministically select the crypto stream universe.

        Preconditions: candidates must be canonical and timestamps must be
        timezone-aware. Pins are force-kept even when they fail a filter and
        are never reported as eliminated; pool membership is enforced by
        ``validate_active_crypto_universe`` at load time.
        """
        if data_timestamp.tzinfo is None or (
            selection_timestamp is not None and selection_timestamp.tzinfo is None
        ):
            raise ValueError("Scanner timestamps must be timezone-aware (UTC)")
        managed_pins = tuple(
            dict.fromkeys(canonical_symbol(item, Market.CRYPTO) for item in pinned_symbols)
        )
        reserved = tuple(dict.fromkeys((*self.config.pinned_symbols, *managed_pins)))
        if len(reserved) > self.config.maximum_stream_symbols:
            raise ValueError("Pinned crypto symbols exceed maximum_stream_symbols")

        seen: set[str] = set()
        eliminated: dict[str, str] = {}
        scored: list[tuple[float, str, str, dict[str, float | str]]] = []
        for item in candidates:
            symbol = canonical_symbol(item.symbol, Market.CRYPTO)
            if symbol in seen:
                raise ValueError(f"Duplicate canonical crypto scanner candidate: {symbol}")
            seen.add(symbol)
            if symbol in self.config.denylist_symbols:
                eliminated[symbol] = "denylist"
                continue
            if self.config.allowlist_symbols and symbol not in self.config.allowlist_symbols:
                eliminated[symbol] = "outside_candidate_pool"
                continue
            reason = self._filter_reason(symbol, item)
            if reason:
                if symbol not in reserved:
                    eliminated[symbol] = reason
                continue
            configured_category = item.category.strip().lower() if item.category else ""
            category = (
                CRYPTO_CATEGORIES.get(symbol, "unknown")
                if configured_category in {"", "unknown"}
                else configured_category
            )
            if category not in {
                "core",
                "layer1",
                "defi",
                "infrastructure",
                "legacy",
                "high_beta",
                "meme",
                "unknown",
            }:
                category = "unknown"
            liquidity = min(
                1.0,
                max(
                    0.0,
                    math.log10(item.median_dollar_volume)
                    / max(1.0, math.log10(self.config.scanner.minimum_median_dollar_volume) + 4),
                ),
            )
            volatility_fit = max(
                0.0,
                1
                - abs(item.atr_pct - self.config.scanner.volatility_optimum_pct)
                / max(
                    0.001,
                    self.config.scanner.maximum_atr_pct - self.config.scanner.minimum_atr_pct,
                ),
            )
            spread_quality = (
                0.5
                if item.spread_bps is None
                else max(0.0, 1 - item.spread_bps / self.config.scanner.maximum_spread_bps)
            )
            diversification = 1.0
            correlation_penalty = 0.0
            if (
                symbol != "BTC/USD"
                and item.btc_correlation is not None
                and item.btc_correlation > self.config.scanner.maximum_btc_correlation
            ):
                correlation_penalty = self.config.scanner.high_correlation_penalty
                diversification = max(0.0, 1.0 - correlation_penalty / 10.0)
            unknown_penalty = (
                self.config.scanner.unknown_category_penalty if category == "unknown" else 0.0
            )
            score = (
                liquidity * 35
                + item.data_completeness * 25
                + volatility_fit * 20
                + spread_quality * 10
                + diversification * 10
                - unknown_penalty
            )
            breakdown: dict[str, float | str] = {
                "total": round(score, 8),
                "liquidity": round(liquidity * 35, 8),
                "data_quality": round(item.data_completeness * 25, 8),
                "volatility_fit": round(volatility_fit * 20, 8),
                "spread_quality": round(spread_quality * 10, 8),
                "diversification": round(diversification * 10, 8),
                "category": category,
                "spread_source": "unavailable" if item.spread_bps is None else "latest_quote",
                "btc_correlation": (
                    "unavailable"
                    if item.btc_correlation is None
                    else round(item.btc_correlation, 8)
                ),
            }
            scored.append((score, symbol, category, breakdown))

        selected = list(reserved)
        breakdowns: dict[str, dict[str, float | str]] = {}
        categories: dict[str, int] = {}
        by_symbol = {symbol: (category, breakdown) for _, symbol, category, breakdown in scored}
        for symbol in reserved:
            category, breakdown = by_symbol.get(
                symbol,
                (
                    CRYPTO_CATEGORIES.get(symbol, "unknown"),
                    {
                        "total": 0.0,
                        "category": CRYPTO_CATEGORIES.get(symbol, "unknown"),
                        "spread_source": "unavailable",
                    },
                ),
            )
            breakdowns[symbol] = {
                **breakdown,
                "selection_role": (
                    "configured_pin" if symbol in self.config.pinned_symbols else "managed_position"
                ),
            }
            categories[category] = categories.get(category, 0) + 1

        for _score, symbol, category, breakdown in sorted(
            scored, key=lambda item: (-item[0], item[1])
        ):
            if symbol in selected:
                continue
            if len(selected) >= self.config.maximum_stream_symbols:
                eliminated.setdefault(symbol, "stream_capacity")
                continue
            limit = self.config.scanner.maximum_per_category
            if category == "meme":
                limit = min(limit, self.config.scanner.maximum_meme_symbols)
            elif category == "high_beta":
                limit = min(limit, self.config.scanner.maximum_high_beta_symbols)
            if categories.get(category, 0) >= limit:
                eliminated[symbol] = "category_concentration"
                continue
            selected.append(symbol)
            categories[category] = categories.get(category, 0) + 1
            breakdowns[symbol] = breakdown

        timestamp = (selection_timestamp or datetime.now(UTC)).astimezone(UTC)
        return CryptoScannerResult(
            selection_timestamp=timestamp.isoformat(),
            data_timestamp=data_timestamp.astimezone(UTC).isoformat(),
            selected_symbols=tuple(selected),
            candidate_symbols=tuple(sorted(seen)),
            score_breakdown=breakdowns,
            eliminated=dict(sorted(eliminated.items())),
            category_distribution=dict(sorted(categories.items())),
            scanner_version=self.version,
            config_digest=crypto_scanner_config_digest(self.config),
        )

    def persist(self, result: CryptoScannerResult) -> None:
        atomic_write(
            Path(self.config.active_universe_path),
            json.dumps(asdict(result), indent=2, sort_keys=True),
        )

    def _filter_reason(self, symbol: str, item: CryptoCandidate) -> str | None:
        try:
            _base, quote = split_crypto_pair(symbol)
        except ValueError:
            return "not_usd_pair"
        if quote != "USD":
            return "not_usd_pair"
        if is_stablecoin_pair(symbol):
            return "stablecoin"
        if not item.tradable or item.status.lower().split(".")[-1] != "active":
            return "not_active_tradable"
        numeric = (
            item.price,
            item.median_dollar_volume,
            item.atr_pct,
            item.data_completeness,
        )
        if any(not math.isfinite(value) for value in numeric) or (
            item.btc_correlation is not None and not math.isfinite(item.btc_correlation)
        ):
            return "invalid_metrics"
        if item.price <= 0:
            return "invalid_metrics"
        if item.history_count <= 0:
            return "insufficient_history"
        if not 0 <= item.data_completeness <= 1:
            return "invalid_metrics"
        if item.data_completeness < self.config.scanner.minimum_data_completeness:
            return "data_completeness"
        if item.median_dollar_volume < self.config.scanner.minimum_median_dollar_volume:
            return "minimum_liquidity"
        if not (
            self.config.scanner.minimum_atr_pct
            <= item.atr_pct
            <= self.config.scanner.maximum_atr_pct
        ):
            return "atr_range"
        if item.spread_bps is not None:
            if not math.isfinite(item.spread_bps) or item.spread_bps < 0:
                return "invalid_metrics"
            if item.spread_bps > self.config.scanner.maximum_spread_bps:
                return "spread_too_wide"
        return None


def crypto_scanner_config_digest(config: CryptoUniverseConfig) -> str:
    payload = json.dumps(asdict(config), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def validate_active_crypto_universe(
    config: CryptoUniverseConfig,
) -> tuple[tuple[str, ...], str]:
    path = Path(config.active_universe_path)
    if not path.exists():
        return (), "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "success":
            return (), "invalid"
        if payload.get("scanner_version") != CryptoUniverseScanner.version:
            return (), "version_mismatch"
        if config.require_scanner_config_digest_match and payload.get(
            "config_digest"
        ) != crypto_scanner_config_digest(config):
            return (), "digest_mismatch"
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
        selected = tuple(canonical_symbol(str(item), Market.CRYPTO) for item in raw)
        if len(selected) != len(set(selected)):
            return (), "invalid"
        if len(selected) > config.maximum_stream_symbols:
            return (), "invalid"
        if not set(config.pinned_symbols).issubset(selected):
            return (), "missing_pins"
        raw_candidates = payload.get("candidate_symbols")
        if not isinstance(raw_candidates, list):
            return (), "invalid"
        candidates = {canonical_symbol(str(item), Market.CRYPTO) for item in raw_candidates}
        managed_pins = {
            symbol
            for symbol in selected
            if isinstance(payload.get("score_breakdown", {}).get(symbol), dict)
            and payload["score_breakdown"][symbol].get("selection_role") == "managed_position"
        }
        if not set(selected).issubset(candidates | set(config.pinned_symbols) | managed_pins):
            return (), "outside_candidate_pool"
        if any(
            split_crypto_pair(symbol)[1] != "USD" or is_stablecoin_pair(symbol)
            for symbol in selected
        ):
            return (), "invalid"
        return selected, "success"
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        # Fail closed, but surface the parse error: a persistent "invalid"
        # status is indistinguishable from rot without the underlying cause.
        logging.getLogger("parayabuck.universe").warning(
            "Active crypto universe validation failed: %s", exc
        )
        return (), "invalid"


def load_active_crypto_symbols(
    config: CryptoUniverseConfig,
    *,
    pinned_symbols: tuple[str, ...] = (),
    log_rejection: bool = True,
) -> tuple[str, ...]:
    """Load a validated scan or use the deterministic configured fallback."""
    managed = tuple(dict.fromkeys(canonical_symbol(item, Market.CRYPTO) for item in pinned_symbols))
    if not config.dynamic_selection_enabled:
        return tuple(dict.fromkeys((*config.trade_symbols, *managed)))

    selected, status = validate_active_crypto_universe(config)
    if status != "success" and log_rejection:
        logging.getLogger("parayabuck.universe").warning(
            "Active crypto universe rejected; status=%s fallback=configured", status
        )
    base = (
        selected
        or tuple(dict.fromkeys((*config.pinned_symbols, *config.trade_symbols)))[
            : config.maximum_stream_symbols
        ]
    )
    reserved = tuple(dict.fromkeys((*config.pinned_symbols, *managed)))
    if len(reserved) > config.maximum_stream_symbols:
        raise RuntimeError("Configured and managed crypto pins exceed maximum_stream_symbols")
    ordered = tuple(dict.fromkeys((*reserved, *base)))
    return ordered[: config.maximum_stream_symbols]


def elimination_reason_summary(eliminated: Mapping[str, str]) -> str:
    """Return deterministic reason counts for scanner CLI/audit output."""
    counts = Counter(eliminated.values())
    return ", ".join(f"{reason}={count}" for reason, count in sorted(counts.items())) or "none"


__all__ = [
    "CRYPTO_CATEGORIES",
    "CryptoCandidate",
    "CryptoScannerResult",
    "CryptoUniverseScanner",
    "crypto_candidate_from_bars",
    "crypto_scanner_config_digest",
    "elimination_reason_summary",
    "load_active_crypto_symbols",
    "validate_active_crypto_universe",
]
