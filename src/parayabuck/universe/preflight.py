from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus
from alpaca.trading.requests import GetAssetsRequest

from parayabuck.config import Config
from parayabuck.core.models import Bar, Market
from parayabuck.data import AlpacaHistoricalData
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import (
    NEW_YORK,
    bar_is_closed,
    bars_to_calendar_days,
    equity_day_bar_is_closed,
)
from parayabuck.universe.crypto_scanner import (
    CryptoScannerResult,
    CryptoUniverseScanner,
    crypto_candidate_from_bars,
    crypto_scanner_config_digest,
    validate_active_crypto_universe,
)
from parayabuck.universe.scanner import (
    EquityCandidate,
    EquityUniverseScanner,
    ScannerResult,
    equity_candidate_from_bars,
    scanner_config_digest,
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


def eligible_equity_assets(config: Config, trading: TradingClient) -> dict[str, Any]:
    """All active, tradable US equities on the supported exchanges (symbol -> asset).

    This is the broad download and scan pool: the point-in-time Stage-A
    shortlist is applied per date inside the backtest universe builder, so a
    symbol liquid in 2022 but absent from today's shortlist can still be
    evaluated on historical data.
    """
    universe = config.universe.equity
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
    return allowed


def eligible_equity_symbols(
    config: Config,
    trading: TradingClient,
    *,
    limit: int | None = None,
) -> list[str]:
    """Sorted broad equity download set, optionally capped by an explicit limit."""
    symbols = sorted(eligible_equity_assets(config, trading))
    return symbols if limit is None else symbols[:limit]


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
    allowed = eligible_equity_assets(config, trading)
    reserved = {
        *universe.reference_symbols,
        *universe.inverse_trade_symbols,
        *universe.allowlist_symbols,
    }
    cached_history: dict[str, list[tuple[str, float, float, float, float, float]]] = {}
    generated_at: datetime | None = None
    refresh_mode = "full"
    try:
        cached_payload = json.loads(source_path.read_text(encoding="utf-8"))
        generated_at = datetime.fromisoformat(str(cached_payload["generated_at"]))
        cache_age = datetime.now(UTC) - generated_at.astimezone(UTC)
        raw_history = cached_payload.get("history")
        if cache_age <= timedelta(days=7) and isinstance(raw_history, dict):
            for symbol, rows in raw_history.items():
                if isinstance(rows, list) and all(isinstance(row, list) for row in rows):
                    cached_history[symbol] = rows
            if cached_history:
                # Rolling 20-day state cache: every eligible symbol keeps its
                # last 20 daily bars locally, so each refresh only fetches the
                # newest daily bars for the whole universe instead of redownloading
                # 45 days. A symbol that became liquid today enters the shortlist
                # tomorrow with fresh metrics, not in up to a week.
                refresh_mode = "incremental"
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    history = AlpacaHistoricalData(
        api_key,
        secret_key,
        stock_feed=config.alpaca.stock_historical_feed,
        data_delayed=config.alpaca.data_delayed,
    )
    end = datetime.now(UTC)
    historical_requests = 0
    bars_downloaded = 0
    by_symbol: dict[str, list[Bar]] = {}
    skip_symbols: set[str] = set()
    if refresh_mode == "incremental":
        missing_history = [symbol for symbol in sorted(allowed) if symbol not in cached_history]
        if missing_history:
            start = end - timedelta(days=45)
            for batch in _chunks(missing_history, 150):
                historical_requests += 1
                downloaded = history.get_bars_batch(batch, "1d", start=start, end=end)
                bars_downloaded += sum(len(items) for items in downloaded.values())
                for symbol in batch:
                    bars = downloaded.get(symbol, [])
                    if bars:
                        cached_history[symbol] = [_bar_to_tuple(bar) for bar in bars[-20:]]
        # Fresh daily bars for the entire eligible universe (one shared
        # trailing window keeps the request count to ~len/150 batches).
        # Symbols whose cached last daily bar is already the latest completed
        # session (fetched after that session closed) need no refetch: the
        # daily bar is final, and a same-slot re-run (retry after an apply
        # failure, startup double-refresh) must not re-download the universe.
        skip_symbols = _equity_delta_skip_symbols(cached_history, generated_at, end)
        for symbol in skip_symbols:
            # Skipped symbols need no refetch (their cached last daily bar is
            # the finalized latest session); their candidates are built from
            # the same cached bars a refetch would return.
            rows = cached_history.get(symbol) or []
            by_symbol[symbol] = _tuples_to_bars(symbol, rows[-20:])
        start = end - timedelta(days=10)
        for batch in _chunks(
            [symbol for symbol in sorted(allowed) if symbol not in skip_symbols], 150
        ):
            historical_requests += 1
            downloaded = history.get_bars_batch(batch, "1d", start=start, end=end)
            bars_downloaded += sum(len(items) for items in downloaded.values())
            for symbol in batch:
                rows = cached_history.setdefault(symbol, [])
                rows.extend(_bar_to_tuple(bar) for bar in downloaded.get(symbol, []))
                rows.sort(key=lambda row: row[0])
                unique: list[tuple[str, float, float, float, float, float]] = []
                for row in rows:
                    if unique and unique[-1][0] == row[0]:
                        # Last write wins: a bar downloaded earlier for the
                        # same day may be an intraday partial; the newer
                        # download replaces it with the (near-)final bar.
                        unique[-1] = row
                    else:
                        unique.append(row)
                cached_history[symbol] = unique[-20:]
                by_symbol[symbol] = _tuples_to_bars(symbol, unique[-20:])
    else:
        start = end - timedelta(days=45)
        for batch in _chunks(sorted(allowed), 150):
            historical_requests += 1
            downloaded = history.get_bars_batch(batch, "1d", start=start, end=end)
            bars_downloaded += sum(len(items) for items in downloaded.values())
            for symbol in batch:
                bars = downloaded.get(symbol, [])
                by_symbol[symbol] = bars
                if bars:
                    cached_history[symbol] = [_bar_to_tuple(bar) for bar in bars[-20:]]
    discovered = [
        _equity_candidate(symbol, allowed[symbol], by_symbol.get(symbol, []))
        for symbol in sorted(allowed)
    ]
    ranked = sorted(
        discovered,
        key=lambda item: (-item.median_dollar_volume_20d, item.symbol),
    )
    shortlist_symbols = {item.symbol for item in ranked[: universe.target_shortlist]} | reserved
    quotes: dict[str, Any] = {}
    try:
        quotes = history.get_latest_stock_quotes(sorted(shortlist_symbols))
    except Exception as exc:
        log.warning(
            "Equity latest quotes unavailable; neutral spread score will be used: %s",
            exc,
        )
    candidates: list[EquityCandidate] = []
    enriched_discovered: list[EquityCandidate] = []
    for item in discovered:
        spread = _spread_bps(quotes.get(item.symbol))
        with_spread = item if spread is None else replace(item, spread_bps=spread)
        enriched_discovered.append(with_spread)
        if item.symbol in shortlist_symbols:
            candidates.append(with_spread)
    scanner = EquityUniverseScanner(universe)
    result = scanner.scan(candidates, data_timestamp=end)
    result = _equity_observation_set(config, history, result, end)

    enriched = {
        "generated_at": end.isoformat(),
        "source": "Alpaca active tradable US equity assets",
        "feed": history.feed_status(),
        "symbols": sorted(allowed),
        "shortlist_symbols": sorted(shortlist_symbols),
        "candidates": [asdict(item) for item in enriched_discovered],
        "history": {
            symbol: rows
            for symbol, rows in sorted(cached_history.items())
            if rows and symbol in allowed
        },
    }
    temporary = source_path.with_suffix(source_path.suffix + ".tmp")
    temporary.write_text(json.dumps(enriched, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(source_path)
    scanner.persist(result)
    log.info(
        "Equity universe refresh metrics: assets_eligible=%d "
        "historical_requests=%d bars_downloaded=%d shortlist_size=%d pool_size=%d "
        "observation_size=%d refresh_mode=%s refreshed_assets=%d skipped_assets=%d "
        "duration_seconds=%.3f",
        len(allowed),
        historical_requests,
        bars_downloaded,
        len(shortlist_symbols),
        len(result.selected_symbols),
        len(result.observation_set),
        refresh_mode,
        len(by_symbol),
        len(skip_symbols),
        time.monotonic() - started,
    )
    return result


def _equity_observation_set(
    config: Config,
    history: AlpacaHistoricalData,
    result: ScannerResult,
    data_timestamp: datetime,
) -> ScannerResult:
    """Replace the scanner's quality observation set with the STRATEGY ALPHA
    ranking of the eligible pool (stream limit != opportunity universe).

    The configured strategy's alpha scores the full pool on batch historical
    data; the top ``maximum_stream_symbols`` plus references/inverse hedges
    become the streamed observation set. Without an enabled strategy the
    scanner's eligibility-quality set stays as the fail-safe stream set.
    """
    from parayabuck.portfolio import portfolio_strategy_from
    from parayabuck.universe.observation_set import build_observation_set

    universe = config.universe.equity
    selection = config.strategy.equity
    if not selection.enabled or not result.selected_symbols:
        return result
    strategy = portfolio_strategy_from(
        selection.name, selection.parameters, timeframe=selection.timeframe
    )
    pool = result.selected_symbols
    warmup_days = bars_to_calendar_days(
        "SPY", strategy.timeframe, strategy.minimum_history_bars, end_date=data_timestamp
    )
    start = data_timestamp - timedelta(days=warmup_days)
    bars = _incremental_history(
        history,
        list(pool),
        strategy.timeframe,
        start,
        data_timestamp,
        scope="equity_observation",
        config_digest=_observation_scope_digest(scanner_config_digest(universe), selection),
        identity=f"{selection.name}:{strategy.timeframe}",
        adjustment="splits",
    )
    observation = build_observation_set(
        strategy,
        {symbol: tuple(bars.get(symbol, ())) for symbol in pool},
        pool,
        market=Market.EQUITY,
        timeframe=strategy.timeframe,
        decision_timestamp=data_timestamp,
        maximum_stream_symbols=universe.maximum_stream_symbols,
        pinned_symbols=(*universe.reference_symbols, *universe.inverse_trade_symbols),
        scanner_fallback=result.observation_set,
    )
    return replace(
        result,
        observation_set=observation.observation_set,
        observation_eliminated={
            **result.observation_eliminated,
            "observation_source": observation.source,
        },
    )


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
        stock_feed=config.alpaca.stock_historical_feed,
        data_delayed=config.alpaca.data_delayed,
    )
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
        identity=(
            f"lookback_days={universe.scanner.lookback_days}:"
            f"timeframe={universe.scanner.historical_timeframe}"
        ),
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
    strategy = portfolio_strategy_from(
        selection.name, selection.parameters, timeframe=selection.timeframe
    )
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
        symbol: tuple(
            bar for bar in bars.get(symbol, ()) if bar_is_closed(bar.timestamp, bar.timeframe, now)
        )
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
        observation_eliminated={
            **result.observation_eliminated,
            "observation_source": observation.source,
        },
    )


def _chunks(values: list[str], size: int = 8) -> list[list[str]]:
    return [values[offset : offset + size] for offset in range(0, len(values), size)]


def _equity_delta_skip_symbols(
    cached_history: dict[str, list[tuple[str, float, float, float, float, float]]],
    generated_at: datetime | None,
    now: datetime,
) -> set[str]:
    """Symbols whose cached daily history can skip the delta refetch.

    The scanner must never read a partial daily bar, so a symbol is skipped
    ONLY when all of the following hold:

    * ``now`` is outside the regular equity session (pre-market before 09:30
      ET or after the 16:00 ET close) — inside the session today's bar is
      forming and must be refetched;
    * the cached last daily bar is the expected latest completed trading day
      (previous weekday pre-market, today after close, previous Friday on
      weekends — holidays conservatively fail open to a refetch);
    * that bar was fetched AFTER its session closed
      (``equity_day_bar_is_closed`` against the payload ``generated_at``), so
      its values are final rather than a mid-session partial.

    Any doubt fails open (symbol refetched), so skipping never changes the
    scanner output — it only avoids re-downloading bars that are provably
    unchanged (the same-slot retry and startup double-refresh cases).
    """
    if generated_at is None:
        return set()
    local = now.astimezone(NEW_YORK)
    if local.weekday() >= 5:
        expected = (local - timedelta(days=local.weekday() - 4)).date()
    elif local.time() < dtime(9, 30):
        expected = (local - timedelta(days=3 if local.weekday() == 0 else 1)).date()
    elif local.time() >= dtime(16, 0):
        expected = local.date()
    else:
        return set()
    skip: set[str] = set()
    for symbol, rows in cached_history.items():
        if not rows:
            continue
        try:
            last_ts = datetime.fromisoformat(rows[-1][0])
        except (TypeError, ValueError):
            continue
        if last_ts.date() != expected:
            continue
        if not equity_day_bar_is_closed(last_ts, generated_at):
            continue
        # Post-close finalization grace: Alpaca can still be finalizing the
        # session bar minutes after 16:00 ET, so a same-day cached bar must
        # be well past the close before its values are trusted as final.
        # Past-date bars are final by construction (their session closed at
        # least a day ago). Any doubt fails open to a refetch.
        bar_local = last_ts.astimezone(NEW_YORK)
        if bar_local.date() == generated_at.astimezone(NEW_YORK).date():
            session_close = bar_local.replace(hour=16, minute=0, second=0, microsecond=0)
            if generated_at < session_close + timedelta(minutes=60):
                continue
        skip.add(symbol)
    return skip


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
    fetcher: Callable[..., dict[str, list[Any]]] | None = getattr(
        history, "get_bars_batch_incremental", None
    )
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
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _bar_to_tuple(bar: Bar) -> tuple[str, float, float, float, float, float]:
    return (
        bar.timestamp.isoformat(),
        float(bar.open),
        float(bar.high),
        float(bar.low),
        float(bar.close),
        float(bar.volume),
    )


def _tuples_to_bars(
    symbol: str,
    rows: list[tuple[str, float, float, float, float, float]],
) -> list[Bar]:
    return [
        Bar(
            datetime.fromisoformat(timestamp),
            symbol,
            "1d",
            open_,
            high,
            low,
            close,
            volume,
            Market.EQUITY,
        )
        for timestamp, open_, high, low, close, volume in rows
    ]


def _equity_candidate(symbol: str, asset: Any, bars: list[Any]) -> EquityCandidate:
    return equity_candidate_from_bars(
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
    "eligible_equity_assets",
    "eligible_equity_symbols",
    "ensure_active_universes",
    "refresh_crypto_universe",
    "refresh_equity_universe",
]
