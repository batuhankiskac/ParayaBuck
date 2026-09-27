from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import parayabuck.cli as cli_module
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine, TerminalLifecycle
from parayabuck.backtest.product import build_product_universe, optimize_grid, ranking_score, run_product_backtest
from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot
from parayabuck.config import Config, StrategyConfig
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.markets.timeframes import bars_to_calendar_days
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for
from parayabuck.portfolio.models import PortfolioState
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
            crypto=base.strategy.crypto,
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
        persist=False,
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
    state = PortfolioState(
        strategy.name,
        strategy.version,
        holdings={"HELD": 1.0},
        avg_prices={"HELD": 100.0},
        cash=99_900.0,
        last_prices={"HELD": 100.0},
    )
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        universe=universe,
        universe_version="test",
        state=state,
    )
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

    base = run_product_backtest(config, _bars(), market=Market.EQUITY, cost_multiplier=1.0, persist=False)
    stressed = run_product_backtest(config, _bars(), market=Market.EQUITY, cost_multiplier=2.0, persist=False)

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


def test_equity_candidate_symbols_are_the_configured_static_universe(tmp_path) -> None:
    # Equity downloads are configuration, not discovery: no eligibility probe,
    # no shortlist file and no candidate cap narrow the configured symbols.
    config = _config(tmp_path)
    assert cli_module._candidate_symbols(config, Market.EQUITY, None, None) == list(config.universe.equity.symbols)
    assert cli_module._candidate_symbols(config, Market.EQUITY, None, 1) == list(config.universe.equity.symbols)
    # An explicit --symbols override still wins over configuration.
    assert cli_module._candidate_symbols(config, Market.EQUITY, "AAPL,MSFT", None) == [
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
    monkeypatch.setattr(cli_module, "ensure_active_universes", lambda *args, **kwargs: None)
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

        def close(self) -> None:
            pass

    monkeypatch.setattr(cli_module, "AlpacaHistoricalData", lambda *a, **k: FakeProvider())
    args = SimpleNamespace(data=None, symbols=None, candidate_limit=None)
    start = datetime(2022, 1, 1, tzinfo=UTC)
    end = datetime(2022, 6, 1, tzinfo=UTC)

    cli_module._load_bars(config, args, Market.EQUITY, start, end)

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
    # Empty downloads (no pre-start history) trigger the bounded warmup
    # extension: at most three fetch attempts.
    assert len(captured_requests) == 3
    assert captured_requests[-1]["start"] <= first["start"]


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


def test_warmup_window_never_shrinks_below_scanner_lookback_for_intraday(
    tmp_path,
) -> None:
    config = _config(tmp_path)
    # For an intraday strategy the strategy-warmup term is measured in bars of
    # that timeframe; the scanner lookback is calendar days and must still be
    # covered in full.
    from dataclasses import replace as _replace

    from parayabuck.config import StrategyConfig

    intraday = _replace(
        config,
        strategy=StrategyConfig(
            equity=config.strategy.equity,
            crypto=_replace(
                config.strategy.crypto,
                parameters={
                    **config.strategy.crypto.parameters,
                    "momentum_scheme": "7d",
                    "rebalance_hours": 24,
                },
            ),
        ),
    )
    lookback = intraday.universe.crypto.scanner.lookback_days
    assert cli_module._warmup_window_days(intraday, Market.CRYPTO) >= lookback


def test_bars_to_calendar_days_scales_by_session_density() -> None:
    from parayabuck.markets.timeframes import bars_to_calendar_days

    # Equity daily bars need more calendar days than bars: at least the
    # no-holiday Mon-Fri density (61*7/5=86), never fewer.
    daily_61 = bars_to_calendar_days("SPY", "1d", 61)
    assert 86 <= daily_61 <= 120
    assert bars_to_calendar_days("SPY", "1d", 150) > 150
    # Intraday equity fits several sessions per calendar day.
    assert bars_to_calendar_days("SPY", "1h", 150) < 60
    # Crypto trades 24/7: the span is close to bars / 24.
    assert bars_to_calendar_days("BTC/USD", "1h", 150) == 7
    assert bars_to_calendar_days("BTC/USD", "1d", 61) == 65


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
            persist=False,
            persist_artifacts=False,
        )


def test_persisted_data_feed_note_never_claims_a_feed_the_run_did_not_get(tmp_path) -> None:
    config = _config(tmp_path)
    bars = _bars(120)
    unknown = run_product_backtest(
        config,
        bars,
        market=Market.EQUITY,
        persist=False,
        persist_artifacts=False,
    )
    assert unknown.report.summary["data_feed"] == (
        "offline csv (feed unknown; downloader used the configured historical feed)"
    )
    assert unknown.report.summary["data_feed_note"] == "Feed unknown; provenance not verified for this offline run."

    sip = run_product_backtest(
        config,
        bars,
        market=Market.EQUITY,
        persist=False,
        persist_artifacts=False,
        data_feed="sip",
    )
    assert sip.report.summary["data_feed_note"] == "SIP consolidates the national market system tape."

    iex = run_product_backtest(
        config,
        bars,
        market=Market.EQUITY,
        persist=False,
        persist_artifacts=False,
        data_feed="iex",
    )
    assert iex.report.summary["data_feed_note"].startswith("IEX historical data reflects one exchange's prints")
