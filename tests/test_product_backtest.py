from __future__ import annotations

import contextlib
import json
import math
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import parayabuck.cli as cli_module
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine, TerminalLifecycle, _Position, session_date_for
from parayabuck.backtest.product import (
    build_product_universe,
    load_csv_bars,
    optimize_grid,
    ranking_score,
    run_product_backtest,
)
from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot
from parayabuck.config import Config, StrategyConfig
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.markets.timeframes import bars_to_calendar_days
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for
from parayabuck.universe import STATIC_EQUITY_UNIVERSE_VERSION


def _bars(days: int = 420) -> list[Bar]:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    symbols = ("SPY", "AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "LOSER", "QQQ", "QLD")
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(symbols):
            if symbol in ("QQQ", "QLD"):
                # The shipped equity identity reads QQQ's SMA and holds QLD;
                # a clean, wobble-free uptrend keeps it invested on every
                # session so the product-runner tests see deterministic trades.
                close = 100 * (1 + (0.0006 if symbol == "QQQ" else 0.0012)) ** index
            else:
                drift = -0.0005 if symbol == "LOSER" else 0.0004 + offset * 0.00002
                # A small deterministic oscillation gives the returns real
                # variance so the volatility-aware product strategy can rank.
                wobble = 1 + 0.01 * math.sin(index * 0.9 + offset)
                close = 100 * (1 + drift) ** index * wobble
            bars.append(
                Bar(
                    timestamp,
                    symbol,
                    "1d",
                    close * 0.999,
                    close * 1.006,
                    close * 0.994,
                    close,
                    2_000_000,
                    Market.EQUITY,
                )
            )
    return bars


def _config(tmp_path) -> Config:
    base = Config()
    parameters = {
        **base.strategy.equity.parameters,
        "sma_bars": 5,
        "vol_target": 0.0,
    }
    return replace(
        base,
        runtime=replace(base.runtime, decision_db_path=str(tmp_path / "runtime.sqlite3")),
        backtest=replace(base.backtest, output_dir=str(tmp_path / "reports")),
        strategy=StrategyConfig(
            equity=replace(base.strategy.equity, parameters=parameters),
        ),
    )


def test_product_backtest_uses_production_strategy_risk_and_next_open_fills(tmp_path) -> None:
    config = _config(tmp_path)
    run = run_product_backtest(config, _bars(), market=Market.EQUITY)
    assert run.result.fills
    assert all(fill.timestamp > fill.decision_timestamp for fill in run.result.fills)
    assert run.report.summary["fill_model"] == "decision on closed bar; next tradable bar open"
    assert run.report.summary["maximum_exposure"] <= config.strategy.equity.maximum_allocation
    assert (tmp_path / "reports" / run.run_id / "drawdowns.csv").exists()
    assert (tmp_path / "reports" / run.run_id / "universe.json").exists()
    report = json.loads((tmp_path / "reports" / run.run_id / "report.json").read_text())
    assert "survivorship_bias_limitation" in report["summary"]
    universe = json.loads((tmp_path / "reports" / run.run_id / "universe.json").read_text())
    assert universe["market"] == "equity"
    assert {symbol for snapshot in universe["snapshots"] for symbol in snapshot["symbols"]} <= set(
        config.universe.equity.symbols
    )
    assert {snapshot["scanner_version"] for snapshot in universe["snapshots"]} == {STATIC_EQUITY_UNIVERSE_VERSION}


def test_product_equity_universe_streams_the_configured_symbols(tmp_path) -> None:
    """Equity has no ranker: the streamed observation set is exactly the
    configured static universe, in configuration order."""
    config = _config(tmp_path)
    bars = _bars()
    universe = build_product_universe(config, bars, market=Market.EQUITY)
    snapshot = universe.snapshots[-1]
    assert snapshot.observation_set == tuple(config.universe.equity.symbols)
    assert snapshot.pinned_symbols == tuple(config.universe.equity.symbols)
    assert snapshot.symbols == frozenset(config.universe.equity.symbols)
    assert snapshot.maximum_stream_symbols == config.universe.equity.maximum_stream_symbols
    # A symbol downloaded into the run but never configured is not a member.
    assert not universe.contains("AAA", Market.EQUITY, bars[-1].timestamp)
    # Every configured symbol joined at its first bar, so one snapshot covers
    # the whole set: the static universe never re-snapshots an unchanged set.
    assert len(universe.snapshots) == 1


def test_requested_strategy_identity_owns_report_but_not_the_static_universe(tmp_path) -> None:
    config = _config(tmp_path)

    run = run_product_backtest(
        config,
        _bars(),
        market=Market.EQUITY,
        strategy_name="equity_leveraged_trend_v1",
    )

    assert run.report.summary["strategy_name"] == "equity_leveraged_trend_v1"
    # The static equity universe is owned by configuration, so the requested
    # strategy arm cannot redefine its streamed membership.
    assert {snapshot.observation_set for snapshot in run.universe.snapshots} == {tuple(config.universe.equity.symbols)}


def test_product_runner_applies_typed_terminal_lifecycle(tmp_path) -> None:
    terminal = datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=350)
    terminal_lifecycles = {"QLD": TerminalLifecycle(terminal, "POSSIBLE_DELISTING")}

    run = run_product_backtest(
        _config(tmp_path),
        _bars(),
        market=Market.EQUITY,
        persist_artifacts=False,
        terminal_lifecycles=terminal_lifecycles,
    )

    terminal_events = [event for event in run.result.terminal_events if event["policy"] == "TERMINAL_BAR"]
    assert [(event["symbol"], event["timestamp"]) for event in terminal_events] == [("QLD", terminal.isoformat())]
    assert terminal_events[0]["terminal_event_class"] == "POSSIBLE_DELISTING"


def test_backtest_context_pins_managed_symbol_within_observation_capacity(tmp_path) -> None:
    config = _config(tmp_path)
    strategy = portfolio_strategy_from(
        config.strategy.equity.name,
        config.strategy.equity.parameters,
        timeframe=config.strategy.equity.timeframe,
    )
    timestamp = datetime(2025, 1, 2, tzinfo=UTC)
    snapshot = UniverseSnapshot(
        timestamp,
        frozenset({"SPY", "A", "B", "C"}),
        observation_set=("SPY", "A", "B", "C"),
        maximum_stream_symbols=4,
        pinned_symbols=("SPY",),
    )
    universe = PointInTimeUniverse(Market.EQUITY, (snapshot,), "test")
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        universe=universe,
        universe_version="test",
    )
    engine._positions["HELD"] = _Position("HELD", Market.EQUITY, "1d", 1.0, 100.0, timestamp)
    engine._cash = 99_900.0
    for symbol in ("SPY", "A", "B", "C", "HELD"):
        bar = Bar(timestamp, symbol, "1d", 100, 101, 99, 100, 1_000_000, Market.EQUITY)
        engine._history[(symbol, "1d")] = [bar]
        engine._last_price[symbol] = 100.0

    assert engine._effective_observation_symbols(timestamp) == ("SPY", "HELD", "A", "B")
    context = engine._build_context(timestamp)
    assert set(context.history) == {"SPY", "HELD", "A", "B"}
    assert context.holdings == {"HELD": 1.0}
    assert not context.in_universe("HELD")


def test_grid_optimizer_ranks_train_and_evaluates_oos_once(tmp_path) -> None:
    config = _config(tmp_path)
    start = datetime(2023, 1, 1, tzinfo=UTC)
    payload = optimize_grid(
        config,
        _bars(),
        market=Market.EQUITY,
        grid={"sma_bars": [5, 6]},
        train_start=start + timedelta(days=100),
        train_end=start + timedelta(days=299),
        oos_start=start + timedelta(days=300),
        oos_end=start + timedelta(days=419),
    )
    training = payload["training_results"]
    assert len(training) == 2
    assert training[0]["score"] >= training[1]["score"]
    assert payload["out_of_sample"]["summary"]["start_date"].startswith("2023")
    assert payload["combination_count"] == 2
    assert len(list((tmp_path / "reports").glob("*/report.json"))) == 1


def test_optimizer_reuses_one_universe_across_all_trials(tmp_path, monkeypatch) -> None:
    """The optimizer builds one requested-strategy universe per window and
    reuses it across every parameter combination."""
    import parayabuck.backtest.product as product_module

    config = _config(tmp_path)
    start = datetime(2023, 1, 1, tzinfo=UTC)
    bars = _bars()
    real_build = product_module.build_product_universe
    builds: list[str] = []
    memberships: list[tuple[tuple[str, ...], ...]] = []

    def counting_build(*args, **kwargs):
        builds.append(kwargs.get("end").isoformat())
        universe = real_build(*args, **kwargs)
        memberships.append(tuple(snapshot.observation_set for snapshot in universe.snapshots))
        return universe

    monkeypatch.setattr(product_module, "build_product_universe", counting_build)

    optimize_grid(
        config,
        bars,
        market=Market.EQUITY,
        grid={"sma_bars": [5, 6, 7]},
        strategy_name="equity_leveraged_trend_v1",
        train_start=start + timedelta(days=100),
        train_end=start + timedelta(days=299),
        oos_start=start + timedelta(days=300),
        oos_end=start + timedelta(days=419),
    )

    # One build for the TRAIN window (shared by 3 trials) + one for OOS.
    assert builds == [
        (start + timedelta(days=299)).isoformat(),
        (start + timedelta(days=419)).isoformat(),
    ]
    # The static equity universe has a single membership, identical in both
    # windows: no parameter arm can redefine the streamed symbols.
    assert memberships[0] == (tuple(config.universe.equity.symbols),)
    assert memberships == [memberships[0], memberships[0]]


@pytest.mark.parametrize(
    ("metric", "expected"),
    (("sharpe", 1.0), ("sortino", 2.0), ("calmar", 3.0)),
)
def test_optimizer_ranking_metric_is_honored(metric: str, expected: float) -> None:
    result = SimpleNamespace(
        sharpe=1.0,
        sortino=2.0,
        calmar=3.0,
        max_drawdown=0.1,
        closed_position_events=tuple(range(5)),
    )

    assert ranking_score(result, metric) == expected


def test_optimizer_rejects_overlapping_train_and_oos_windows(tmp_path) -> None:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="must not overlap"):
        optimize_grid(
            _config(tmp_path),
            _bars(),
            market=Market.EQUITY,
            grid={"sma_bars": [5]},
            train_start=start,
            train_end=start + timedelta(days=300),
            oos_start=start + timedelta(days=200),
            oos_end=start + timedelta(days=400),
        )

    with pytest.raises(ValueError, match="explicit TRAIN"):
        optimize_grid(
            _config(tmp_path),
            _bars(),
            market=Market.EQUITY,
            grid={"sma_bars": [5]},
            oos_start=start + timedelta(days=300),
            oos_end=start + timedelta(days=400),
        )


def test_optimizer_uses_configured_metric_for_full_trial_ordering(tmp_path) -> None:
    config = _config(tmp_path)
    config = replace(
        config,
        backtest=replace(config.backtest, ranking_metric="sharpe"),
    )
    payload = optimize_grid(
        config,
        _bars(280),
        market=Market.EQUITY,
        grid={"sma_bars": [5, 6]},
    )
    training = payload["training_results"]

    assert payload["ranking"] == "sharpe"
    assert all(item["score"] == item["sharpe"] for item in training)
    assert training[0]["sharpe"] >= training[1]["sharpe"]


def test_optimizer_rejects_excessive_grid_before_running_trials(tmp_path) -> None:
    with pytest.raises(ValueError, match="maximum is 256"):
        optimize_grid(
            _config(tmp_path),
            _bars(),
            market=Market.EQUITY,
            grid={"a": list(range(17)), "b": list(range(17))},
        )


def test_backtest_cli_runs_offline_csv_end_to_end(tmp_path, monkeypatch, capsys) -> None:
    bars = [bar for bar in _bars(220) if bar.timestamp.weekday() < 5]
    csv_path = tmp_path / "bars.csv"
    rows = ["timestamp,symbol,timeframe,open,high,low,close,volume,market"]
    rows.extend(
        ",".join(
            (
                bar.timestamp.isoformat(),
                bar.symbol,
                bar.timeframe,
                str(bar.open),
                str(bar.high),
                str(bar.low),
                str(bar.close),
                str(bar.volume),
                bar.market.value,
            )
        )
        for bar in bars
    )
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    config = _config(tmp_path)
    monkeypatch.setattr(cli_module, "load_config", lambda _path: config)

    exit_code = cli_module.main(
        [
            "backtest",
            "--market",
            "equity",
            "--strategy",
            "equity_leveraged_trend_v1",
            "--data",
            str(csv_path),
            "--output-dir",
            str(tmp_path / "cli-reports"),
        ]
    )

    assert exit_code == 0
    assert "equity_leveraged_trend_v1" in capsys.readouterr().out
    assert list((tmp_path / "cli-reports").glob("*/report.json"))


def test_equity_product_passes_regulatory_fees_to_execution_engine(tmp_path) -> None:
    config = _config(tmp_path)
    config = replace(
        config,
        backtest=replace(
            config.backtest,
            equity_use_effective_dated_fees=False,
            equity_taf_per_share=0.0002,
            equity_taf_cap=10.0,
            equity_cat_per_share=0.00003,
            equity_sec_fee_rate=0.00002,
        ),
    )

    run = run_product_backtest(config, _bars(), market=Market.EQUITY)

    assert run.result.fee_total > 0


def test_product_backtest_credits_explicit_dividend_once(tmp_path) -> None:
    config = _config(tmp_path)
    bars = _bars()
    action = CorporateAction(
        datetime(2023, 10, 28, tzinfo=UTC),
        "QLD",
        CorporateActionType.DIVIDEND,
        1.0,
    )

    run = run_product_backtest(
        config,
        bars,
        market=Market.EQUITY,
        strategy_name="equity_leveraged_trend_v1",
        corporate_actions=[action],
    )

    assert run.result.dividend_total > 0
    assert "explicit dividend cash flows" in run.report.summary["price_adjustment_model"]


def test_product_cost_multiplier_includes_equity_regulatory_fees(tmp_path) -> None:
    config = _config(tmp_path)
    config = replace(
        config,
        backtest=replace(
            config.backtest,
            slippage_bps=0.0,
            spread_bps=0.0,
            commission=0.0,
            equity_use_effective_dated_fees=False,
            equity_taf_per_share=0.0002,
            equity_taf_cap=10.0,
            equity_cat_per_share=0.00003,
            equity_sec_fee_rate=0.00002,
        ),
    )

    base = run_product_backtest(config, _bars(), market=Market.EQUITY, cost_multiplier=1.0)
    stressed = run_product_backtest(config, _bars(), market=Market.EQUITY, cost_multiplier=2.0)

    assert base.result.fee_total > 0
    # Higher fees slightly reduce later order quantities, so the portfolio's
    # aggregate fee total is nearly, but not algebraically, twice the base run.
    assert stressed.result.fee_total == pytest.approx(base.result.fee_total * 2.0, rel=5e-6)
    assert stressed.report.summary["regulatory_fee_model"]["cost_multiplier"] == 2.0


def test_equity_csv_rejects_weekend_execution_bars(tmp_path) -> None:
    csv_path = tmp_path / "weekend.csv"
    csv_path.write_text(
        "timestamp,symbol,timeframe,open,high,low,close,volume,market\n"
        "2025-01-04T00:00:00+00:00,SPY,1d,100,101,99,100,1000,equity\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="weekend"):
        cli_module.load_csv_bars(csv_path, default_market=Market.EQUITY)


def test_product_backtest_is_equity_only(tmp_path) -> None:
    csv_path = tmp_path / "crypto.csv"
    csv_path.write_text(
        "timestamp,symbol,timeframe,open,high,low,close,volume,market\n"
        "2025-01-06T00:00:00+00:00,BTC/USD,1d,100,101,99,100,1000,crypto\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="equity only"):
        load_csv_bars(csv_path, default_market=Market.EQUITY)
    config = _config(tmp_path)
    with pytest.raises(ValueError, match="equity only"):
        run_product_backtest(config, _bars(), market=Market.CRYPTO, persist_artifacts=False)
    with pytest.raises(ValueError, match="equity only"):
        build_product_universe(config, _bars(), market=Market.CRYPTO)


def test_equity_candidate_symbols_are_the_configured_static_universe(tmp_path) -> None:
    # Equity downloads are configuration, not discovery: no eligibility probe,
    # no shortlist file and no candidate cap narrow the configured symbols.
    config = _config(tmp_path)
    assert cli_module._candidate_symbols(config, Market.EQUITY, None) == list(config.universe.equity.symbols)
    # An explicit --symbols override still wins over configuration.
    assert cli_module._candidate_symbols(config, Market.EQUITY, "AAPL,MSFT") == [
        "AAPL",
        "MSFT",
    ]


def test_load_bars_downloads_warmup_history_before_requested_start(tmp_path, monkeypatch) -> None:
    # _load_bars intentionally fails closed without credentials; provide
    # non-production test values so the provider itself can be replaced below
    # without relying on a local .env file.
    monkeypatch.setenv("ALPACA_API_KEY", "unit-test-api-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "unit-test-secret-key")
    config = _config(tmp_path)
    captured_requests: list[dict[str, object]] = []

    class FakeProvider:
        def get_bars_batch(
            self,
            symbols: list[str],
            timeframe: str,
            *,
            start: datetime,
            end: datetime,
            adjustment: str,
        ) -> dict[str, list[Bar]]:
            captured_requests.append(
                {
                    "symbols": tuple(symbols),
                    "timeframe": timeframe,
                    "start": start,
                    "end": end,
                    "adjustment": adjustment,
                }
            )
            return {symbol: [] for symbol in symbols}

        def feed_status(self) -> dict[str, object]:
            # A configured SIP request that hit an entitlement downgrade.
            return {"configured": "sip", "effective": "iex", "sip_fallback_active": True}

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli_module, "AlpacaHistoricalData", lambda *a, **k: FakeProvider())
    args = SimpleNamespace(data=None, symbols=None)
    start = datetime(2022, 1, 1, tzinfo=UTC)
    end = datetime(2022, 6, 1, tzinfo=UTC)

    _bars_downloaded, data_feed = cli_module._load_bars(config, args, Market.EQUITY, start, end)

    # The report must carry the feed the bars actually came from, not the
    # configured one: an entitlement downgrade serves IEX prints.
    assert data_feed == "iex"

    assert captured_requests
    first = captured_requests[0]
    # The warmup span is anchored at the evaluation start (2022 session
    # density), never at today's date.
    expected_warmup = timedelta(days=cli_module._warmup_window_days(config, Market.EQUITY, end_date=start))
    assert first["start"] == start - expected_warmup
    assert first["end"] == end
    assert first["adjustment"] == "splits"
    assert first["timeframe"] == "1d"
    assert first["symbols"] == config.universe.equity.symbols
    # Empty downloads (no pre-start history) cannot be fixed by the warmup
    # extension: its window already equals the strategy warmup span, so the
    # loop stops instead of re-downloading the same window.
    assert len(captured_requests) == 1


def test_load_bars_returns_the_single_window_download_for_a_deficient_symbol(tmp_path, monkeypatch) -> None:
    # One symbol has full pre-start history, one (a late listing) has none:
    # the warmup extension cannot move the window, so exactly one download
    # happens and the returned bars are that download's bars, unchanged.
    monkeypatch.setenv("ALPACA_API_KEY", "unit-test-api-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "unit-test-secret-key")
    config = _config(tmp_path)
    start = datetime(2022, 1, 1, tzinfo=UTC)
    end = datetime(2022, 3, 1, tzinfo=UTC)
    requests: list[tuple[datetime, datetime]] = []

    def series(symbol: str, first: datetime, last: datetime) -> list[Bar]:
        bars: list[Bar] = []
        cursor = first
        while cursor <= last:
            bars.append(Bar(cursor, symbol, "1d", 10.0, 11.0, 9.0, 10.5, 1_000.0))
            cursor += timedelta(days=1)
        return bars

    class FakeProvider:
        def get_bars_batch(self, symbols, timeframe, *, start, end, adjustment):  # type: ignore[no-untyped-def]
            requests.append((start, end))
            late_listing = datetime(2022, 2, 1, tzinfo=UTC)
            return {symbol: series(symbol, late_listing if symbol == "QLD" else start, end) for symbol in symbols}

        def feed_status(self) -> dict[str, object]:
            return {"configured": "sip", "effective": "sip", "sip_fallback_active": False}

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli_module, "AlpacaHistoricalData", lambda *a, **k: FakeProvider())
    args = SimpleNamespace(data=None, symbols=None)

    bars, data_feed = cli_module._load_bars(config, args, Market.EQUITY, start, end)

    assert data_feed == "sip"

    assert len(requests) == 1
    window_start, window_end = requests[0]
    expected = [
        bar
        for symbol in config.universe.equity.symbols
        for bar in series(symbol, datetime(2022, 2, 1, tzinfo=UTC) if symbol == "QLD" else window_start, window_end)
    ]
    assert bars == expected


def test_warmup_window_covers_strategy_minimum_history_for_equity(
    tmp_path,
) -> None:
    # The SHIPPED DEFAULT strategy (equity_leveraged_trend_v1) declares
    # minimum_history_bars=200 daily bars — not the reduced-window fixture
    # config used by the other backtest tests.
    base = Config()
    config = replace(
        base,
        runtime=replace(base.runtime, decision_db_path=str(tmp_path / "runtime.sqlite3")),
    )
    # The warmup window counts REAL NYSE sessions (holiday-aware), so it
    # spans more calendar days than bars: at least the no-holiday Mon-Fri
    # minimum (ceil(200*7/5)=280) and bounded by a plausible holiday count.
    # The static equity universe adds no scanner lookback on top of it.
    assert cli_module._warmup_window_days(config, Market.EQUITY) == bars_to_calendar_days(
        "SPY", "1d", 200, end_date=None
    )


def test_bars_to_calendar_days_scales_by_session_density() -> None:
    from datetime import date

    from parayabuck.markets.timeframes import bars_to_calendar_days

    # Fixed anchors keep this deterministic: the span depends on the anchor's
    # weekday and nearby holidays, so an implicit "today" anchor made the
    # bound flaky. The window [anchor - span, anchor] is INCLUSIVE of both
    # ends (span + 1 dates), so 61 Mon-Fri sessions need at least
    # (61 - 1) * 7 / 5 = 84 days of span with no holidays, never fewer.
    # Tuesday 2026-09-29 (Labor Day inside the window) needs exactly 85;
    # a Friday anchor needs 86.
    assert bars_to_calendar_days("SPY", "1d", 61, end_date=date(2026, 9, 29)) == 85
    assert bars_to_calendar_days("SPY", "1d", 61, end_date=date(2026, 9, 25)) == 86
    for anchor in (date(2026, 9, 28), date(2026, 9, 29), date(2026, 7, 1), date(2025, 12, 31)):
        daily_61 = bars_to_calendar_days("SPY", "1d", 61, end_date=anchor)
        assert 84 <= daily_61 <= 120
        assert bars_to_calendar_days("SPY", "1d", 150, end_date=anchor) > 150
        # Intraday equity fits several sessions per calendar day.
        assert bars_to_calendar_days("SPY", "1h", 150, end_date=anchor) < 60


def test_configured_symbol_without_bars_never_blocks_periods(tmp_path) -> None:
    """A configured symbol with no bars in the run is never a member, so the
    decision gate is not made fail-closed for the whole window."""
    config = _config(tmp_path)
    bars = _bars(days=40)
    config = replace(
        config,
        universe=replace(
            config.universe,
            equity=replace(
                config.universe.equity,
                symbols=(*config.universe.equity.symbols, "TQQQ"),
            ),
        ),
    )
    assert "TQQQ" not in {bar.symbol for bar in bars}
    universe = build_product_universe(config, bars, market=Market.EQUITY)
    assert universe.snapshots
    available = {bar.symbol for bar in bars}
    for snapshot in universe.snapshots:
        assert "TQQQ" not in snapshot.symbols
        assert all(symbol in available for symbol in snapshot.symbols)
        assert all(symbol in available for symbol in snapshot.observation_set)


def test_explicit_zero_starting_equity_is_rejected_not_replaced_by_config(tmp_path) -> None:
    """``initial_cash=0.0`` is an explicit request, not "unset": it must reach
    the engine's own positivity guard instead of silently falling back to
    ``config.backtest.initial_cash``."""
    with pytest.raises(ValueError, match="Starting equity must be positive"):
        run_product_backtest(
            _config(tmp_path),
            _bars(120),
            market=Market.EQUITY,
            initial_cash=0.0,
            persist_artifacts=False,
        )


def test_persisted_data_feed_note_never_claims_a_feed_the_run_did_not_get(tmp_path) -> None:
    config = _config(tmp_path)
    bars = _bars(120)
    unknown = run_product_backtest(
        config,
        bars,
        market=Market.EQUITY,
        persist_artifacts=False,
    )
    assert unknown.report.summary["data_feed"] == "unknown (no provider feed was reported for this run)"
    assert unknown.report.summary["data_feed_note"] == "Feed unknown; provenance not verified for this offline run."

    sip = run_product_backtest(
        config,
        bars,
        market=Market.EQUITY,
        persist_artifacts=False,
        data_feed="sip",
    )
    assert sip.report.summary["data_feed_note"] == "SIP consolidates the national market system tape."

    iex = run_product_backtest(
        config,
        bars,
        market=Market.EQUITY,
        persist_artifacts=False,
        data_feed="iex",
    )
    assert iex.report.summary["data_feed_note"].startswith("IEX historical data reflects one exchange's prints")


def test_direct_run_normalizes_unsorted_warmup_like_the_optimizer(tmp_path) -> None:
    # Warmup reached the engine in caller order on the
    # direct path; the engine keeps only strictly newer bars per series, so
    # without normalization the warmup history silently collapses.
    config = _config(tmp_path)
    bars = _bars()
    start = datetime(2023, 1, 1, tzinfo=UTC) + timedelta(days=200)
    warmup_first = sorted(
        (bar for bar in bars if bar.timestamp < start),
        key=lambda bar: bar.timestamp,
        reverse=True,
    )
    shuffled = warmup_first + [bar for bar in bars if bar.timestamp >= start]
    kwargs = {"market": Market.EQUITY, "start": start, "persist_artifacts": False}

    baseline = run_product_backtest(config, bars, **kwargs)
    unsorted = run_product_backtest(config, shuffled, **kwargs)

    assert baseline.result.equity_curve == unsorted.result.equity_curve
    assert [fill.timestamp for fill in baseline.result.fills] == [fill.timestamp for fill in unsorted.result.fills]


def _csv_bars(path: Path, bars: list[Bar]) -> Path:
    rows = ["timestamp,symbol,timeframe,open,high,low,close,volume,market"]
    rows.extend(
        ",".join(
            (
                bar.timestamp.isoformat(),
                bar.symbol,
                bar.timeframe,
                str(bar.open),
                str(bar.high),
                str(bar.low),
                str(bar.close),
                str(bar.volume),
                bar.market.value,
            )
        )
        for bar in bars
    )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _table_names(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with contextlib.closing(sqlite3.connect(path)) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row[0] for row in rows}


def test_cli_backtest_never_writes_research_rows_into_the_runtime_decision_db(tmp_path, monkeypatch) -> None:
    # F41: the CLI backtest (and the web dashboard's one-off backtest, which
    # shells it) must not inject research rows into the live decision ledger
    # that the operator also backs up as the decision record.
    csv_path = _csv_bars(tmp_path / "bars.csv", [bar for bar in _bars(220) if bar.timestamp.weekday() < 5])
    config = _config(tmp_path)
    decision_db = tmp_path / "runtime.sqlite3"
    assert config.runtime.decision_db_path == str(decision_db)
    monkeypatch.setattr(cli_module, "load_config", lambda _path: config)

    exit_code = cli_module.main(
        [
            "backtest",
            "--market",
            "equity",
            "--strategy",
            "equity_leveraged_trend_v1",
            "--data",
            str(csv_path),
            "--output-dir",
            str(tmp_path / "cli-reports"),
        ]
    )

    assert exit_code == 0
    assert "backtest_runs" not in _table_names(decision_db)
    assert not (tmp_path / "cli-reports" / "backtest_runs.sqlite3").exists()


def test_offline_csv_backtest_report_never_claims_the_configured_sip_feed(tmp_path, monkeypatch) -> None:
    # F42: the configured feed is only a claim; a --data csv run never asks
    # Alpaca, so the persisted report must say the feed is unknown.
    csv_path = _csv_bars(tmp_path / "bars.csv", [bar for bar in _bars(220) if bar.timestamp.weekday() < 5])
    config = _config(tmp_path)
    assert config.alpaca.stock_historical_feed == "sip"
    monkeypatch.setattr(cli_module, "load_config", lambda _path: config)
    reports = tmp_path / "feed-reports"

    exit_code = cli_module.main(
        [
            "backtest",
            "--market",
            "equity",
            "--strategy",
            "equity_leveraged_trend_v1",
            "--data",
            str(csv_path),
            "--output-dir",
            str(reports),
        ]
    )

    assert exit_code == 0
    summary = json.loads(next(reports.glob("*/report.json")).read_text(encoding="utf-8"))["summary"]
    assert summary["data_feed"] == "unknown (no provider feed was reported for this run)"
    assert summary["data_feed_note"] == "Feed unknown; provenance not verified for this offline run."


def test_equity_csv_requires_a_volume_column(tmp_path) -> None:
    """A missing volume column used to read as 0.0, which deferred every
    order and produced a silent zero-trade backtest."""
    csv_path = tmp_path / "no_volume.csv"
    csv_path.write_text(
        "timestamp,symbol,open,high,low,close\n2025-01-06T00:00:00+00:00,SPY,100,101,99,100\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing required columns: volume"):
        load_csv_bars(csv_path, default_market=Market.EQUITY)


def test_equity_csv_rejects_a_blank_volume(tmp_path) -> None:
    csv_path = tmp_path / "blank_volume.csv"
    csv_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n2025-01-06T00:00:00+00:00,SPY,100,101,99,100,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="row 2 has no volume for SPY"):
        load_csv_bars(csv_path, default_market=Market.EQUITY)


def test_naive_daily_csv_timestamp_is_the_new_york_session_date(tmp_path) -> None:
    """A naive daily stamp read as 00:00 UTC is the PREVIOUS New York date."""
    csv_path = tmp_path / "naive.csv"
    csv_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2025-01-06,SPY,100,101,99,100,1000\n"
        "2025-07-07T00:00:00,SPY,100,101,99,100,1000\n"
        "2025-01-07T05:00:00+00:00,SPY,100,101,99,100,1000\n",
        encoding="utf-8",
    )
    winter, summer, aware = load_csv_bars(csv_path, default_market=Market.EQUITY)

    assert winter.timestamp == datetime(2025, 1, 6, 5, tzinfo=UTC)
    assert summer.timestamp == datetime(2025, 7, 7, 4, tzinfo=UTC)
    assert session_date_for(winter.timestamp).isoformat() == "2025-01-06"
    assert session_date_for(summer.timestamp).isoformat() == "2025-07-07"
    # Timezone-aware stamps are taken as given.
    assert aware.timestamp == datetime(2025, 1, 7, 5, tzinfo=UTC)


def test_backtest_without_start_treats_the_strategy_warmup_as_warmup(tmp_path) -> None:
    """Without --start the first decidable session opens the evaluation window;
    the forced-cash warmup no longer dilutes CAGR/Sharpe."""
    config = _config(tmp_path)
    bars = _bars(60)
    strategy = portfolio_strategy_from(
        config.strategy.equity.name, config.strategy.equity.parameters, timeframe=config.strategy.equity.timeframe
    )
    first_decidable = sorted({bar.timestamp for bar in bars})[strategy.minimum_history_bars - 1]

    run = run_product_backtest(config, bars, market=Market.EQUITY, persist_artifacts=False)
    explicit = run_product_backtest(config, bars, market=Market.EQUITY, start=first_decidable, persist_artifacts=False)

    assert run.result.start_date == first_decidable.isoformat()
    assert run.result.equity_curve == explicit.result.equity_curve
    assert run.report.summary["evaluation_start_source"] == (
        f"first bar + {strategy.minimum_history_bars}-bar strategy warmup"
    )
    assert explicit.report.summary["evaluation_start_source"] == "requested"


def test_backtest_without_enough_history_evaluates_every_bar(tmp_path) -> None:
    config = _config(tmp_path)
    bars = [bar for bar in _bars(30) if bar.symbol != "QLD" or bar.timestamp.day == 1]
    run = run_product_backtest(config, bars, market=Market.EQUITY, persist_artifacts=False)
    assert run.result.start_date == min(bar.timestamp for bar in bars).isoformat()
    assert run.report.summary["evaluation_start_source"] == "full history"


def test_price_adjustment_model_states_whether_dividends_were_credited(tmp_path) -> None:
    config = _config(tmp_path)
    plain = run_product_backtest(config, _bars(60), market=Market.EQUITY, persist_artifacts=False)
    assert "no dividend cash flows" in plain.report.summary["price_adjustment_model"]
    dividend = CorporateAction(datetime(2023, 2, 1, tzinfo=UTC), "QLD", CorporateActionType.DIVIDEND, 0.1)
    with_actions = run_product_backtest(
        config, _bars(60), market=Market.EQUITY, corporate_actions=[dividend], persist_artifacts=False
    )
    assert "explicit dividend cash flows" in with_actions.report.summary["price_adjustment_model"]
