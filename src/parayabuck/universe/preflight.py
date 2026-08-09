from __future__ import annotations

import json
import logging
import statistics
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
from parayabuck.features.indicators import true_ranges
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
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    configured = {str(item).upper() for item in payload.get("symbols", ()) if isinstance(item, str)}
    if not configured:
        configured = set(universe.fallback_trade_symbols)
    if not configured:
        raise ValueError("Equity candidate universe is empty")

    trading = TradingClient(api_key, secret_key, paper=config.alpaca.paper)
    if (session := getattr(trading, "_session", None)) is not None:
        patch_session_timeout(session, timeout=15.0)
    assets = trading.get_all_assets(
        GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE)
    )
    allowed = {
        str(asset.symbol): asset
        for asset in assets
        if str(asset.symbol) in configured and bool(getattr(asset, "tradable", False))
    }
    history = AlpacaHistoricalData(
        api_key,
        secret_key,
        stock_feed=config.alpaca.stock_feed,
        data_delayed=config.alpaca.data_delayed,
    )
    end = datetime.now(UTC)
    start = end - timedelta(days=45)
    candidates: list[EquityCandidate] = []
    for batch in _chunks(sorted(allowed)):
        by_symbol = history.get_bars_batch(batch, "1d", start=start, end=end)
        candidates.extend(
            _equity_candidate(symbol, allowed[symbol], by_symbol.get(symbol, []))
            for symbol in batch
        )
    scanner = EquityUniverseScanner(universe)
    result = scanner.scan(candidates, data_timestamp=end)

    enriched = {
        "generated_at": end.isoformat(),
        "source": "Alpaca active tradable US equity assets + repository fallback",
        "symbols": sorted(configured),
        "candidates": [asdict(item) for item in candidates],
    }
    temporary = source_path.with_suffix(source_path.suffix + ".tmp")
    temporary.write_text(json.dumps(enriched, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(source_path)
    scanner.persist(result)
    return result


def refresh_crypto_universe(
    config: Config,
    api_key: str,
    secret_key: str,
) -> CryptoScannerResult:
    universe = config.universe.crypto
    trading = TradingClient(api_key, secret_key, paper=config.alpaca.paper)
    if (session := getattr(trading, "_session", None)) is not None:
        patch_session_timeout(session, timeout=15.0)
    assets = trading.get_all_assets(
        GetAssetsRequest(asset_class=AssetClass.CRYPTO, status=AssetStatus.ACTIVE)
    )
    by_symbol = {
        symbol: asset
        for asset in assets
        if (symbol := _crypto_asset_symbol(asset)) is not None and symbol in universe.trade_symbols
    }
    history = AlpacaHistoricalData(
        api_key,
        secret_key,
        stock_feed=config.alpaca.stock_feed,
        data_delayed=config.alpaca.data_delayed,
    )
    end = datetime.now(UTC)
    start = end - timedelta(days=universe.scanner.lookback_days)
    bars = history.get_bars_batch(
        list(universe.trade_symbols),
        universe.scanner.historical_timeframe,
        start=start,
        end=end,
    )
    quotes: dict[str, Any] = {}
    try:
        quotes = history.get_latest_crypto_quotes(list(universe.trade_symbols))
    except Exception as exc:
        log.warning(
            "Crypto latest quotes unavailable; neutral spread score will be used: %s",
            exc,
        )

    expected = universe.scanner.lookback_days * 24
    btc_bars = bars.get("BTC/USD", [])
    candidates = []
    for symbol in universe.trade_symbols:
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
    recent = bars[-20:]
    if not recent:
        return EquityCandidate(symbol, 0, 0, 0, 0)
    dollar_volumes = [bar.close * bar.volume for bar in recent]
    ranges = true_ranges(recent)
    price = recent[-1].close
    name = str(getattr(asset, "name", "")).lower()
    leveraged = any(
        marker in name for marker in ("2x", "3x", "-2x", "-3x", "ultra", "inverse", "bear", "short")
    )
    return EquityCandidate(
        symbol=symbol,
        price=price,
        median_dollar_volume_20d=statistics.median(dollar_volumes),
        atr_pct=(statistics.mean(ranges) / price if ranges and price > 0 else 0),
        data_completeness=min(1.0, len(recent) / 20),
        economic_sector=(
            str(getattr(asset, "sector", "") or getattr(asset, "economic_sector", "")).strip()
            or None
        ),
        exchange=str(getattr(asset, "exchange", "")).split(".")[-1],
        tradable=bool(getattr(asset, "tradable", False)),
        status=str(getattr(asset, "status", "")).lower().split(".")[-1],
        leveraged_or_inverse=leveraged,
    )


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
