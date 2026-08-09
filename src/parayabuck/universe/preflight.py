from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from parayabuck.config import Config
from parayabuck.core.models import Market
from parayabuck.data import AlpacaHistoricalData
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.universe.crypto_scanner import (
    CryptoScannerResult,
    CryptoUniverseScanner,
    crypto_candidate_from_bars,
    validate_active_crypto_universe,
)
from parayabuck.universe.scanner import (
    EquityCandidate,
    EquityUniverseScanner,
    ScannerResult,
    equity_candidate_from_bars,
    validate_active_universe,
)
from parayabuck.utils.http import patch_session_timeout

log = logging.getLogger("parayabuck.universe")


@dataclass(frozen=True)
class UniversePreflightResult:
    equity_status: str
    crypto_status: str
    equity_refreshed: bool = False
    crypto_refreshed: bool = False


def _refresh_with_retry(refresh: Callable[[], object], name: str) -> bool:
    """Run one universe refresh with a single retry on transient failures.

    Returns True when the refresh succeeded. Failures surface through the
    returned False (the caller reports an explicit ``refresh_failed`` status)
    instead of being swallowed silently.
    """
    import time as _time

    for attempt in range(2):
        try:
            refresh()
            return True
        except Exception as exc:
            log.warning(
                "Automatic %s universe scan attempt %d failed; fallback=configured error=%s",
                name,
                attempt + 1,
                exc,
            )
            if attempt == 0:
                _time.sleep(1.0)
    return False


def ensure_active_universes(
    config: Config,
    api_key: str,
    secret_key: str,
) -> UniversePreflightResult:
    """Refresh missing/stale dynamic universes before runtime subscriptions are built."""
    equity_status = "disabled"
    crypto_status = "disabled"
    equity_refreshed = False
    crypto_refreshed = False

    equity = config.universe.equity
    if equity.enabled and equity.dynamic_selection_enabled:
        _, equity_status = validate_active_universe(equity)
        if equity_status != "success":
            log.info("Automatic equity universe scan started; reason=%s", equity_status)
            if _refresh_with_retry(
                lambda: refresh_equity_universe(config, api_key, secret_key), "equity"
            ):
                equity_status = "success"
                equity_refreshed = True
                log.info("Automatic equity universe scan completed")
            else:
                equity_status = "refresh_failed"

    crypto = config.universe.crypto
    if crypto.enabled and crypto.dynamic_selection_enabled:
        _, crypto_status = validate_active_crypto_universe(crypto)
        if crypto_status != "success":
            log.info("Automatic crypto universe scan started; reason=%s", crypto_status)
            if _refresh_with_retry(
                lambda: refresh_crypto_universe(config, api_key, secret_key), "crypto"
            ):
                crypto_status = "success"
                crypto_refreshed = True
                log.info("Automatic crypto universe scan completed")
            else:
                crypto_status = "refresh_failed"

    return UniversePreflightResult(
        equity_status=equity_status,
        crypto_status=crypto_status,
        equity_refreshed=equity_refreshed,
        crypto_refreshed=crypto_refreshed,
    )


def refresh_equity_universe(
    config: Config,
    api_key: str,
    secret_key: str,
) -> ScannerResult:
    universe = config.universe.equity
    source_path = Path(universe.candidate_universe_path)
    trading = TradingClient(api_key, secret_key, paper=config.runtime.mode.value != "live")
    if (session := getattr(trading, "_session", None)) is not None:
        patch_session_timeout(session, timeout=15.0)
    started = time.monotonic()
    assets = trading.get_all_assets(
        GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE)
    )
    allowed: dict[str, Any] = {}
    allowlist = set(universe.allowlist_symbols)
    denylist = set(universe.denylist_symbols)
    for asset in assets:
        symbol = str(getattr(asset, "symbol", "")).strip().upper()
        exchange = str(getattr(asset, "exchange", "")).upper().split(".")[-1]
        if (
            not symbol
            or "/" in symbol
            or not bool(getattr(asset, "tradable", False))
            or exchange not in universe.supported_exchanges
            or symbol in denylist
            or (allowlist and symbol not in allowlist)
        ):
            continue
        allowed[symbol] = asset
    if not allowed:
        raise ValueError("Alpaca returned no eligible active US equities")
    reserved = {
        *universe.reference_symbols,
        *universe.inverse_trade_symbols,
        *universe.allowlist_symbols,
    }
    cached_candidates: dict[str, EquityCandidate] = {}
    refresh_symbols = set(allowed)
    refresh_mode = "full"
    try:
        cached_payload = json.loads(source_path.read_text(encoding="utf-8"))
        generated_at = datetime.fromisoformat(str(cached_payload["generated_at"]))
        cache_age = datetime.now(UTC) - generated_at.astimezone(UTC)
        raw_candidates = cached_payload.get("candidates", ())
        if cache_age <= timedelta(days=7) and isinstance(raw_candidates, list):
            for raw in raw_candidates:
                if not isinstance(raw, dict):
                    continue
                candidate = EquityCandidate(**raw)
                if candidate.symbol in allowed:
                    cached_candidates[candidate.symbol] = candidate
            previous_shortlist = {
                str(symbol) for symbol in cached_payload.get("shortlist_symbols", ())
            }
            new_symbols = set(allowed) - set(cached_candidates)
            incremental = (previous_shortlist | new_symbols | reserved) & set(allowed)
            if cached_candidates and incremental:
                refresh_symbols = incremental
                refresh_mode = "incremental"
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    history = AlpacaHistoricalData(
        api_key,
        secret_key,
        stock_feed=config.alpaca.stock_feed,
        data_delayed=config.alpaca.data_delayed,
    )
    end = datetime.now(UTC)
    start = end - timedelta(days=45)
    refreshed_candidates: list[EquityCandidate] = []
    historical_requests = 0
    bars_downloaded = 0
    for batch in _chunks(sorted(refresh_symbols), 150):
        historical_requests += 1
        by_symbol = history.get_bars_batch(batch, "1d", start=start, end=end)
        bars_downloaded += sum(len(items) for items in by_symbol.values())
        refreshed_candidates.extend(
            _equity_candidate(symbol, allowed[symbol], by_symbol.get(symbol, []))
            for symbol in batch
        )
    merged_candidates = dict(cached_candidates)
    merged_candidates.update((candidate.symbol, candidate) for candidate in refreshed_candidates)
    discovered = list(merged_candidates.values())
    ranked = sorted(
        discovered,
        key=lambda item: (-item.median_dollar_volume_20d, item.symbol),
    )
    shortlist_symbols = {item.symbol for item in ranked[: universe.target_shortlist]} | reserved
    candidates = [item for item in discovered if item.symbol in shortlist_symbols]
    scanner = EquityUniverseScanner(universe)
    result = scanner.scan(candidates, data_timestamp=end)

    enriched = {
        "generated_at": end.isoformat(),
        "source": "Alpaca active tradable US equity assets",
        "symbols": sorted(allowed),
        "shortlist_symbols": sorted(shortlist_symbols),
        "candidates": [asdict(item) for item in discovered],
    }
    temporary = source_path.with_suffix(source_path.suffix + ".tmp")
    temporary.write_text(json.dumps(enriched, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(source_path)
    scanner.persist(result)
    log.info(
        "Equity universe refresh metrics: assets_discovered=%d assets_eligible=%d "
        "historical_requests=%d bars_downloaded=%d shortlist_size=%d selected_count=%d "
        "refresh_mode=%s refreshed_assets=%d duration_seconds=%.3f",
        len(assets),
        len(allowed),
        historical_requests,
        bars_downloaded,
        len(shortlist_symbols),
        len(result.selected_symbols),
        refresh_mode,
        len(refresh_symbols),
        time.monotonic() - started,
    )
    return result


def refresh_crypto_universe(
    config: Config,
    api_key: str,
    secret_key: str,
) -> CryptoScannerResult:
    universe = config.universe.crypto
    trading = TradingClient(api_key, secret_key, paper=config.runtime.mode.value != "live")
    if (session := getattr(trading, "_session", None)) is not None:
        patch_session_timeout(session, timeout=15.0)
    assets = trading.get_all_assets(
        GetAssetsRequest(asset_class=AssetClass.CRYPTO, status=AssetStatus.ACTIVE)
    )
    by_symbol: dict[str, Any] = {}
    allowlist = set(universe.allowlist_symbols)
    denylist = set(universe.denylist_symbols)
    for asset in assets:
        symbol = _crypto_asset_symbol(asset)
        if (
            symbol is None
            or not symbol.endswith("/USD")
            or symbol in denylist
            or (allowlist and symbol not in allowlist)
        ):
            continue
        by_symbol[symbol] = asset
    invalid_pins = [
        symbol
        for symbol in universe.pinned_symbols
        if symbol not in by_symbol
        or not bool(getattr(by_symbol[symbol], "tradable", False))
        or str(getattr(by_symbol[symbol], "status", "")).lower().split(".")[-1] != "active"
    ]
    if invalid_pins:
        raise ValueError(
            f"Configured crypto pins are not active/tradable: {','.join(invalid_pins)}"
        )
    candidate_symbols = tuple(sorted(by_symbol))
    if not candidate_symbols:
        raise ValueError("Alpaca returned no supported USD crypto pairs")
    history = AlpacaHistoricalData(
        api_key,
        secret_key,
        stock_feed=config.alpaca.stock_feed,
        data_delayed=config.alpaca.data_delayed,
    )
    end = datetime.now(UTC)
    start = end - timedelta(days=universe.scanner.lookback_days)
    bars = history.get_bars_batch(
        list(candidate_symbols),
        universe.scanner.historical_timeframe,
        start=start,
        end=end,
    )
    quotes: dict[str, Any] = {}
    try:
        quotes = history.get_latest_crypto_quotes(list(candidate_symbols))
    except Exception as exc:
        log.warning(
            "Crypto latest quotes unavailable; neutral spread score will be used: %s",
            exc,
        )

    expected = universe.scanner.lookback_days * 24
    btc_bars = bars.get("BTC/USD", [])
    candidates = []
    for symbol in candidate_symbols:
        asset = by_symbol.get(symbol)
        candidates.append(
            crypto_candidate_from_bars(
                symbol,
                bars.get(symbol, []),
                expected_bar_count=expected,
                tradable=bool(asset is not None and getattr(asset, "tradable", False)),
                status=str(getattr(asset, "status", "inactive")).lower().split(".")[-1],
                btc_bars=btc_bars,
                spread_bps=_spread_bps(quotes.get(symbol)),
            )
        )
    scanner = CryptoUniverseScanner(universe)
    result = scanner.scan(candidates, data_timestamp=end)
    scanner.persist(result)
    return result


def _chunks(values: list[str], size: int = 8) -> list[list[str]]:
    return [values[offset : offset + size] for offset in range(0, len(values), size)]


def _equity_candidate(symbol: str, asset: Any, bars: list[Any]) -> EquityCandidate:
    candidate = equity_candidate_from_bars(
        symbol,
        bars,
        expected_bar_count=20,
        economic_sector=(
            str(getattr(asset, "sector", "") or getattr(asset, "economic_sector", "")).strip()
            or None
        ),
        exchange=str(getattr(asset, "exchange", "")).split(".")[-1],
        tradable=bool(getattr(asset, "tradable", False)),
        status=str(getattr(asset, "status", "")).lower().split(".")[-1],
    )
    return candidate


def _crypto_asset_symbol(asset: Any) -> str | None:
    raw = str(getattr(asset, "symbol", "")).strip().upper()
    if not raw:
        return None
    try:
        return canonical_symbol(raw, Market.CRYPTO)
    except ValueError:
        return f"{raw[:-3]}/USD" if raw.endswith("USD") and len(raw) > 3 else None


def _spread_bps(quote: Any) -> float | None:
    try:
        bid = float(quote.bid_price)
        ask = float(quote.ask_price)
        mid = (bid + ask) / 2
        return ((ask - bid) / mid) * 10_000 if bid >= 0 and ask >= bid and mid > 0 else None
    except (AttributeError, TypeError, ValueError):
        return None


__all__ = [
    "UniversePreflightResult",
    "ensure_active_universes",
    "refresh_crypto_universe",
    "refresh_equity_universe",
]
