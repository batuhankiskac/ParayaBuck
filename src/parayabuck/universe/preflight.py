from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from parayabuck.config import Config
from parayabuck.core.models import Market
from parayabuck.data import AlpacaHistoricalData
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import (
    bar_is_closed,
    bars_to_calendar_days,
)
from parayabuck.universe.crypto_scanner import (
    CryptoScannerResult,
    CryptoUniverseScanner,
    crypto_candidate_from_bars,
    crypto_scanner_config_digest,
    validate_active_crypto_universe,
)
from parayabuck.utils.http import patch_session_timeout

log = logging.getLogger("parayabuck.universe")


@dataclass(frozen=True)
class UniversePreflightResult:
    equity_status: str
    crypto_status: str
    crypto_refreshed: bool = False


def _close_refresh_resource(resource: Any | None) -> None:
    """Close one refresh-owned client without masking the primary failure."""
    if resource is None:
        return
    close = getattr(resource, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:
            log.warning("Universe refresh client close warning: %s", exc)
        return
    session = getattr(resource, "_session", None)
    if session is not None:
        try:
            session.close()
        except Exception as exc:
            log.warning("Universe refresh session close warning: %s", exc)


def _refresh_with_retry(refresh: Callable[[], object], name: str) -> bool:
    """Run one universe refresh with a single bounded retry on transient failures.

    Bounded retries (exactly one retry after a 1 s backoff): the scan's
    batched download must not be hammered during an Alpaca outage. A persistent failure returns False and
    the caller reports ``refresh_failed`` so production policy can fail closed
    or keep the last known verified snapshot with its explicit stale age.
    Retries are bounded to prevent thundering-herd and to keep provider-call
    budgets predictable.
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
    """Refresh a missing/stale crypto universe before runtime subscriptions are built.

    The equity universe is static (``universe.equity.symbols``) and reports
    ``"static"`` when enabled. Crypto semantics:
    * Pagination/cache — history is fetched through the ``WarmupCache``
      incremental path in bounded batches.
    * Retries — one bounded retry (see ``_refresh_with_retry``) on transient
      failures; no unbounded loops.
    * Stale snapshot with explicit age — ``validate_active_crypto_universe``
      computes staleness from the snapshot timestamps and returns ``"stale"``.
    * No partial universe on failure — the scanner writes its JSON atomically;
      an exception before the replace leaves the previous file untouched. On
      ``refresh_failed`` the runtime keeps the last verified snapshot when
      ``allow_static_fallback_trading`` is set, otherwise it fails closed
      (``invalid_dynamic_universe`` halt).
    """
    equity_status = "static" if config.universe.equity.enabled else "disabled"
    crypto_status = "disabled"
    crypto_refreshed = False

    crypto = config.universe.crypto
    if crypto.enabled and crypto.dynamic_selection_enabled:
        _, crypto_status = validate_active_crypto_universe(crypto)
        if crypto_status != "success":
            log.info("Automatic crypto universe scan started; reason=%s", crypto_status)
            if _refresh_with_retry(lambda: refresh_crypto_universe(config, api_key, secret_key), "crypto"):
                crypto_status = "success"
                crypto_refreshed = True
                log.info("Automatic crypto universe scan completed")
            else:
                crypto_status = "refresh_failed"

    return UniversePreflightResult(
        equity_status=equity_status,
        crypto_status=crypto_status,
        crypto_refreshed=crypto_refreshed,
    )


def refresh_crypto_universe(
    config: Config,
    api_key: str,
    secret_key: str,
) -> CryptoScannerResult:
    """Refresh the crypto universe and close clients owned by this call."""
    trading: Any | None = None
    history: AlpacaHistoricalData | None = None

    def make_history() -> AlpacaHistoricalData:
        nonlocal history
        history = AlpacaHistoricalData(
            api_key,
            secret_key,
            stock_feed=config.alpaca.stock_historical_feed,
            data_delayed=config.alpaca.data_delayed,
        )
        return history

    try:
        trading = TradingClient(api_key, secret_key, paper=config.runtime.mode.value != "live")
        if (session := getattr(trading, "_session", None)) is not None:
            patch_session_timeout(session, timeout=15.0)
        assets = trading.get_all_assets(GetAssetsRequest(asset_class=AssetClass.CRYPTO, status=AssetStatus.ACTIVE))
        return _refresh_crypto_universe_with_clients(
            config,
            assets,
            history_factory=make_history,
        )
    finally:
        _close_refresh_resource(history)
        _close_refresh_resource(trading)


def _refresh_crypto_universe_with_clients(
    config: Config,
    assets: list[Any],
    *,
    history_factory: Callable[[], AlpacaHistoricalData],
) -> CryptoScannerResult:
    universe = config.universe.crypto
    # Paper candidate-pool parity: the authoritative alpha-search pool is the
    # configured trade_symbols (the supported configured universe). The broker's
    # currently supported active/tradable assets are an intersection, not a
    # replacement. Unconfigured broker symbols (e.g. FOO/USD) must never enter
    # the alpha candidate pool, and a configured symbol that the broker reports
    # inactive/non-tradable must be excluded from fresh ENTRY eligibility.
    # Managed positions outside the configured pool remain observable for safe
    # exits via load_active_crypto_symbols(reserved pinning), not via the
    # candidate pool.
    broker_active: dict[str, Any] = {}
    allowlist = set(universe.allowlist_symbols)
    denylist = set(universe.denylist_symbols)
    for asset in assets:
        symbol = _crypto_asset_symbol(asset)
        if symbol is None or not symbol.endswith("/USD"):
            continue
        broker_active[symbol] = asset
    # Configured pool (authoritative) filtered by allow/deny deterministically.
    configured_pool = set(universe.trade_symbols)
    if allowlist:
        configured_pool &= allowlist
    if denylist:
        configured_pool -= denylist
    # Also apply denylist to broker map for pinned validation consistency,
    # but candidate intersection uses configured_pool only.
    invalid_pins = [
        symbol
        for symbol in universe.pinned_symbols
        if symbol not in broker_active
        or symbol in denylist
        or (allowlist and symbol not in allowlist)
        or not bool(getattr(broker_active[symbol], "tradable", False))
        or str(getattr(broker_active[symbol], "status", "")).lower().split(".")[-1] != "active"
    ]
    if invalid_pins:
        raise ValueError(f"Configured crypto pins are not active/tradable: {','.join(invalid_pins)}")
    # Intersect configured pool with broker active/tradable.
    candidate_symbols_list: list[str] = []
    for symbol in sorted(configured_pool):
        asset = broker_active.get(symbol)
        if asset is None:
            continue
        if not bool(getattr(asset, "tradable", False)):
            continue
        if str(getattr(asset, "status", "")).lower().split(".")[-1] != "active":
            continue
        candidate_symbols_list.append(symbol)
    # Keep a filtered broker map for candidate construction only.
    by_symbol: dict[str, Any] = {symbol: broker_active[symbol] for symbol in candidate_symbols_list}
    candidate_symbols = tuple(candidate_symbols_list)
    if not candidate_symbols:
        raise ValueError("No configured crypto trade symbols are currently active/tradable (broker intersection empty)")
    history = history_factory()
    end = datetime.now(UTC)
    start = end - timedelta(days=universe.scanner.lookback_days)
    bars = _incremental_history(
        history,
        list(candidate_symbols),
        universe.scanner.historical_timeframe,
        start,
        end,
        scope="crypto_scanner",
        config_digest=crypto_scanner_config_digest(universe),
        identity=(f"lookback_days={universe.scanner.lookback_days}:timeframe={universe.scanner.historical_timeframe}"),
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
    result = _crypto_observation_set(config, history, result, end)
    scanner.persist(result)
    return result


def _crypto_observation_set(
    config: Config,
    history: AlpacaHistoricalData,
    result: CryptoScannerResult,
    data_timestamp: datetime,
) -> CryptoScannerResult:
    """Strategy-alpha observation set for crypto (same contract as equity):
    the configured strategy ranks the eligible pool on batch history and the
    top ``maximum_stream_symbols`` plus pins become the streamed set."""
    from parayabuck.portfolio import portfolio_strategy_from
    from parayabuck.universe.observation_set import build_observation_set

    universe = config.universe.crypto
    selection = config.strategy.crypto
    if not selection.enabled or not result.selected_symbols:
        return result
    strategy = portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
    pool = result.selected_symbols
    warmup_days = bars_to_calendar_days(
        "BTC/USD", strategy.timeframe, strategy.minimum_history_bars, end_date=data_timestamp
    )
    start = data_timestamp - timedelta(days=warmup_days)
    bars = _incremental_history(
        history,
        list(pool),
        strategy.timeframe,
        start,
        data_timestamp,
        scope="crypto_observation",
        config_digest=_observation_scope_digest(crypto_scanner_config_digest(universe), selection),
        identity=f"{selection.name}:{strategy.timeframe}",
    )
    # Alpaca's feed includes the forming (in-progress) bar for the current
    # bucket; its partial close must never rank the observation set.

    now = datetime.now(UTC)
    closed_bars = {
        symbol: tuple(bar for bar in bars.get(symbol, ()) if bar_is_closed(bar.timestamp, bar.timeframe, now))
        for symbol in pool
    }
    observation = build_observation_set(
        strategy,
        closed_bars,
        pool,
        market=Market.CRYPTO,
        timeframe=strategy.timeframe,
        decision_timestamp=data_timestamp,
        maximum_stream_symbols=universe.maximum_stream_symbols,
        pinned_symbols=universe.pinned_symbols,
        scanner_fallback=result.observation_set,
    )
    return replace(
        result,
        observation_set=observation.observation_set,
        observation_strategy_name=strategy.name,
        observation_strategy_version=strategy.version,
        observation_strategy_timeframe=strategy.timeframe,
        observation_strategy_parameters_digest=universe.observation_strategy_parameters_digest
        or hashlib.sha256(json.dumps(selection.parameters, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        observation_eliminated={
            **result.observation_eliminated,
            "observation_source": observation.source,
        },
    )


def _incremental_history(
    history: Any,
    symbols: list[str],
    timeframe: str,
    start: datetime,
    end: datetime,
    *,
    scope: str,
    config_digest: str,
    identity: str = "",
    adjustment: str = "raw",
) -> dict[str, list[Any]]:
    """Fetch history through the WarmupCache incremental path when available.

    Falls back to a plain ``get_bars_batch`` for clients that do not expose
    the incremental fetcher (test fakes), preserving the pre-optimization
    contract exactly. The incremental path validates the cache scope digest,
    window head coverage and tail freshness (fail closed to a full refetch)
    inside ``AlpacaHistoricalData``; the returned merged bars are identical
    to a full-window download.
    """
    fetcher: Callable[..., dict[str, list[Any]]] | None = getattr(history, "get_bars_batch_incremental", None)
    if fetcher is None:
        plain: dict[str, list[Any]] = history.get_bars_batch(
            symbols, timeframe, start=start, end=end, adjustment=adjustment
        )
        return plain
    merged: dict[str, list[Any]] = fetcher(
        symbols,
        timeframe,
        start=start,
        end=end,
        adjustment=adjustment,
        scope=scope,
        config_digest=config_digest,
        identity=identity,
    )
    return merged


def _observation_scope_digest(base_digest: str, selection: object) -> str:
    """Cache-scope digest for a strategy observation-set fetch.

    The observation window is sized by the strategy's minimum-history
    requirement, so the strategy identity (name, parameters, timeframe) must
    gate cache reuse: any change fails closed to a full refetch instead of
    silently serving a shorter window.
    """
    payload = {
        "base": base_digest,
        "name": getattr(selection, "name", ""),
        "timeframe": getattr(selection, "timeframe", None),
        "parameters": getattr(selection, "parameters", {}),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


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
]
