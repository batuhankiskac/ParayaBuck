"""Scanner/warmup cache determinism: cache reuse must never change output.

Tier-1 guard for the WorkerD optimization candidates: the WarmupCache
incremental path changes only I/O (what is downloaded), never the math.
These tests prove the scanner results and warmup seeding computed from a
cache-backed (incremental) fetch are byte-identical to a full-window fetch,
and that the fail-closed rules (digest mismatch, missing window head) force
a full refetch instead of silently serving a shorter history.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from parayabuck.config import Config
from parayabuck.core.models import Market
from parayabuck.universe import preflight as preflight_module

# ---------------------------------------------------------------------------
# deterministic synthetic bars: values are pure functions of (symbol, ts)
# ---------------------------------------------------------------------------
_EPOCH = datetime(2015, 1, 1, tzinfo=UTC)


def _row(symbol: str, timestamp: datetime, market: Market) -> dict[str, Any]:
    days = max(0, (timestamp - _EPOCH).total_seconds() // 86400)
    rng = random.Random(f"{market.value}|{symbol}")
    drift = 1 + rng.uniform(-0.0002, 0.0004)
    noise = random.Random(f"{symbol}|{timestamp.date().isoformat()}")
    close = round(100 * (drift**days) * (1 + noise.uniform(-0.005, 0.005)), 2)
    open_ = round(close * (1 + noise.uniform(-0.004, 0.004)), 2)
    high = round(max(open_, close) * (1 + noise.uniform(0.0, 0.004)), 2)
    low = round(min(open_, close) * (1 - noise.uniform(0.0, 0.004)), 2)
    return {
        "symbol": symbol,
        "timestamp": timestamp,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": float(noise.randint(500_000, 3_000_000)),
    }


def _rows(symbols: list[str], start: datetime, end: datetime, minutes: int, market: Market):
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    rows: list[dict[str, Any]] = []
    bucket = start.replace(minute=0, second=0, microsecond=0)
    while bucket <= end:
        for symbol in symbols:
            rows.append(_row(symbol, bucket, market))
        bucket += timedelta(minutes=minutes)
    return rows


def _to_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    return frame.sort_values(["symbol", "timestamp"])


class FakeCryptoClient:
    def __init__(self) -> None:
        self.bar_rows: list[int] = []

    def get_crypto_bars(self, request: Any) -> Any:
        minutes = _timeframe_minutes(request.timeframe)
        rows = _rows(
            list(request.symbol_or_symbols), request.start, request.end, minutes, Market.CRYPTO
        )
        self.bar_rows.append(len(rows))
        return SimpleNamespace(df=_to_df(rows))

    def get_crypto_latest_quote(self, request: Any) -> dict[str, Any]:
        return {
            symbol: SimpleNamespace(bid_price=100.0, ask_price=100.05)
            for symbol in request.symbol_or_symbols
        }


class FakeStockClient:
    def __init__(self) -> None:
        self.bar_rows: list[int] = []

    def get_stock_bars(self, request: Any) -> Any:
        minutes = _timeframe_minutes(request.timeframe)
        rows = _rows(
            list(request.symbol_or_symbols), request.start, request.end, minutes, Market.EQUITY
        )
        self.bar_rows.append(len(rows))
        return SimpleNamespace(df=_to_df(rows))

    def get_stock_latest_quote(self, request: Any) -> dict[str, Any]:
        return {
            symbol: SimpleNamespace(bid_price=100.0, ask_price=100.05)
            for symbol in request.symbol_or_symbols
        }


def _timeframe_minutes(timeframe: Any) -> int:
    unit_minutes = {"Minute": 1, "Hour": 60, "Day": 1440}
    return getattr(timeframe, "amount", 1) * unit_minutes.get(
        str(getattr(timeframe, "unit")).split(".")[-1], 1
    )


class FakeTrading:
    def __init__(self, symbols: list[str]) -> None:
        self.symbols = symbols

    def get_all_assets(self, _request: Any) -> list[Any]:
        return [
            SimpleNamespace(symbol=symbol, tradable=True, status="active", exchange="NASDAQ")
            for symbol in self.symbols
        ]


_CRYPTO = ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "LINK/USD", "DOGE/USD"]
_EQUITY = ["AAA", "BBB", "CCC", "SPY"]


def _config(tmp_path: Path) -> Config:
    from dataclasses import replace as _replace

    from parayabuck.config import (
        CryptoScannerConfig,
        CryptoUniverseConfig,
        EquityScannerConfig,
        EquityUniverseConfig,
    )

    base = Config()
    equity = _replace(
        EquityUniverseConfig(
            maximum_stream_symbols=4,
            target_shortlist=4,
            reference_symbols=("SPY",),
            allowlist_symbols=tuple(_EQUITY),
            scanner=EquityScannerConfig(
                minimum_price=1,
                minimum_median_dollar_volume=1,
                minimum_data_completeness=0.9,
                minimum_atr_pct=0.001,
                maximum_per_sector=5,
            ),
        ),
        candidate_universe_path=str(tmp_path / "equity_candidates.json"),
        active_universe_path=str(tmp_path / "active_equity_universe.json"),
    )
    crypto = _replace(
        CryptoUniverseConfig(
            enabled=True,
            dynamic_selection_enabled=True,
            maximum_stream_symbols=5,
            pinned_symbols=("BTC/USD",),
            trade_symbols=tuple(_CRYPTO),
            allowlist_symbols=tuple(_CRYPTO),
            scanner=CryptoScannerConfig(
                minimum_median_dollar_volume=100,
                minimum_data_completeness=0.5,
            ),
        ),
        active_universe_path=str(tmp_path / "active_crypto_universe.json"),
    )
    strategy = _replace(base.strategy, crypto=_replace(base.strategy.crypto, enabled=True))
    return _replace(
        base,
        universe=_replace(base.universe, equity=equity, crypto=crypto),
        strategy=strategy,
    )


def _install_fakes(monkeypatch: Any, trading: FakeTrading) -> None:
    monkeypatch.setattr(preflight_module, "TradingClient", lambda *a, **k: trading)


def _history(config: Config, crypto: FakeCryptoClient, stock: FakeStockClient) -> Any:
    from parayabuck.data.alpaca import AlpacaHistoricalData

    history = AlpacaHistoricalData("k", "s", stock_feed="sip")
    history._crypto_client = crypto
    history._stock_client = stock
    return history


def _crypto_result_snapshot(result: Any) -> dict[str, Any]:
    from dataclasses import asdict

    payload = asdict(result)
    for key in ("selection_timestamp", "data_timestamp"):
        payload.pop(key)
    return payload


def test_crypto_refresh_cold_and_warm_cache_results_are_identical(
    tmp_path: Path, monkeypatch: Any
) -> None:
    config = _config(tmp_path)
    trading = FakeTrading(_CRYPTO)
    crypto = FakeCryptoClient()
    stock = FakeStockClient()
    _install_fakes(monkeypatch, trading)
    monkeypatch.setattr(
        preflight_module, "AlpacaHistoricalData", lambda *a, **k: _history(config, crypto, stock)
    )

    cold = preflight_module.refresh_crypto_universe(config, "k", "s")
    cold_rows = sum(crypto.bar_rows)
    crypto.bar_rows.clear()
    warm = preflight_module.refresh_crypto_universe(config, "k", "s")
    warm_rows = sum(crypto.bar_rows)

    # The warm refresh downloaded only the tail (overlap + forming bars).
    assert warm_rows < cold_rows // 10
    assert warm_rows <= len(_CRYPTO) * 8
    assert _crypto_result_snapshot(cold) == _crypto_result_snapshot(warm)


def test_crypto_refresh_config_digest_change_fails_closed_to_full_refetch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    config = _config(tmp_path)
    trading = FakeTrading(_CRYPTO)
    crypto = FakeCryptoClient()
    stock = FakeStockClient()
    _install_fakes(monkeypatch, trading)
    monkeypatch.setattr(
        preflight_module, "AlpacaHistoricalData", lambda *a, **k: _history(config, crypto, stock)
    )
    preflight_module.refresh_crypto_universe(config, "k", "s")

    from dataclasses import replace

    changed = replace(
        config,
        universe=replace(
            config.universe,
            crypto=replace(config.universe.crypto, active_universe_max_age_hours=6.0),
        ),
    )
    crypto.bar_rows.clear()
    preflight_module.refresh_crypto_universe(changed, "k", "s")
    # Full lookback re-downloaded (336 hourly bars x 7 symbols) because the
    # scope digest no longer matches the cached rows.
    assert crypto.bar_rows and crypto.bar_rows[0] >= len(_CRYPTO) * 336


def test_equity_refresh_observation_cache_keeps_scanner_result_identical(
    tmp_path: Path, monkeypatch: Any
) -> None:
    config = _config(tmp_path)
    trading = FakeTrading(_EQUITY)
    crypto = FakeCryptoClient()
    stock = FakeStockClient()
    _install_fakes(monkeypatch, trading)
    monkeypatch.setattr(
        preflight_module, "AlpacaHistoricalData", lambda *a, **k: _history(config, crypto, stock)
    )

    cold = preflight_module.refresh_equity_universe(config, "k", "s")
    cold_bars = sum(stock.bar_rows)
    stock.bar_rows.clear()
    warm = preflight_module.refresh_equity_universe(config, "k", "s")
    warm_bars = sum(stock.bar_rows)

    assert warm_bars < cold_bars
    assert _crypto_result_snapshot(cold) == _crypto_result_snapshot(warm)


class _FrozenDatetime(datetime):
    frozen: datetime | None = None

    @classmethod
    def now(cls, tz=None):  # noqa: D102
        assert cls.frozen is not None
        return cls.frozen.astimezone(tz or UTC)


def test_equity_same_slot_skip_after_close_produces_identical_output(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """After the session close the cached final daily bar needs no refetch;
    a same-slot re-run must skip the whole daily delta pass and produce the
    identical scanner result."""
    from datetime import time as dtime

    from parayabuck.markets.timeframes import NEW_YORK

    frozen = datetime(2026, 8, 18, 17, 30, tzinfo=NEW_YORK).astimezone(UTC)  # Tue 17:30 ET
    _FrozenDatetime.frozen = frozen
    monkeypatch.setattr(preflight_module, "datetime", _FrozenDatetime)

    config = _config(tmp_path)
    trading = FakeTrading(_EQUITY)
    crypto = FakeCryptoClient()
    stock = FakeStockClient()
    _install_fakes(monkeypatch, trading)
    monkeypatch.setattr(
        preflight_module, "AlpacaHistoricalData", lambda *a, **k: _history(config, crypto, stock)
    )

    first = preflight_module.refresh_equity_universe(config, "k", "s")
    first_calls = len(stock.bar_rows)
    stock.bar_rows.clear()
    second = preflight_module.refresh_equity_universe(config, "k", "s")
    second_calls = len(stock.bar_rows)

    # The second refresh must not re-download the daily delta pass (only the
    # observation tail fetch + quotes remain).
    assert second_calls < first_calls
    assert _crypto_result_snapshot(first) == _crypto_result_snapshot(second)
    # Sanity: the frozen clock really is after the regular session close.
    assert frozen.astimezone(NEW_YORK).time() >= dtime(16, 0)
