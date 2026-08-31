from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import parayabuck.cli as cli_module
from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.product import build_product_universe, optimize_grid, ranking_score, run_product_backtest
from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot
from parayabuck.config import Config, StrategyConfig
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for
from parayabuck.portfolio.models import PortfolioState
from parayabuck.universe.observation_set import build_observation_set


def _bars(days: int = 420) -> list[Bar]:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    symbols = ("SPY", "AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "LOSER")
    bars: list[Bar] = []
    for index in range(days):
        timestamp = start + timedelta(days=index)
        for offset, symbol in enumerate(symbols):
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
        "formation_bars": 60,
        "skip_bars": 5,
        "beta_window_bars": 80,
        "residual_vol_window": 20,
        "top_n": 4,
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
    assert 0.0 <= universe["sector_metadata_coverage"] <= 1.0
    assert universe["sector_metadata_coverage"] == report["summary"]["sector_metadata_coverage"]


def test_product_universe_observation_uses_configured_strategy_alpha(tmp_path) -> None:
    config = _config(tmp_path)
    bars = _bars()
    universe = build_product_universe(config, bars, market=Market.EQUITY)
    snapshot = universe.snapshots[-1]
    strategy = portfolio_strategy_from(
        config.strategy.equity.name,
        config.strategy.equity.parameters,
        timeframe=config.strategy.equity.timeframe,
    )
    history = {
        symbol: tuple(bar for bar in bars if bar.symbol == symbol and bar.timestamp < snapshot.effective_at)
        for symbol in snapshot.symbols
    }
    data_timestamp = max(bar.timestamp for series in history.values() for bar in series)
    direct = build_observation_set(
        strategy,
        history,
        tuple(sorted(snapshot.symbols)),
        market=Market.EQUITY,
        timeframe=strategy.timeframe,
        decision_timestamp=data_timestamp,
        maximum_stream_symbols=snapshot.maximum_stream_symbols,
        pinned_symbols=snapshot.pinned_symbols,
    )
    assert direct.alpha_scored_count > 0
    assert snapshot.observation_strategy_name == strategy.name
    assert snapshot.observation_strategy_version == strategy.version
    assert snapshot.observation_set[: len(direct.observation_set)] == direct.observation_set


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
        grid={"formation_bars": [60, 66]},
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
    """The point-in-time universe depends only on the bar set and scanner
    policy, never on strategy parameters: the optimizer must build it once
    per window and reuse it for every combination."""
    import parayabuck.backtest.product as product_module

    config = _config(tmp_path)
    start = datetime(2023, 1, 1, tzinfo=UTC)
    bars = _bars()
    real_build = product_module.build_product_universe
    builds: list[str] = []

    def counting_build(*args, **kwargs):
        builds.append(kwargs.get("end").isoformat())
        return real_build(*args, **kwargs)

    monkeypatch.setattr(product_module, "build_product_universe", counting_build)

    optimize_grid(
        config,
        bars,
        market=Market.EQUITY,
        grid={"formation_bars": [60, 66, 72]},
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
            grid={"formation_bars": [60]},
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
            grid={"formation_bars": [60]},
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
        grid={"formation_bars": [60, 66]},
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
            "equity_residual_momentum_v1",
            "--data",
            str(csv_path),
            "--output-dir",
            str(tmp_path / "cli-reports"),
        ]
    )

    assert exit_code == 0
    assert "equity_residual_momentum_v1" in capsys.readouterr().out
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
        "FFF",
        CorporateActionType.DIVIDEND,
        1.0,
    )

    run = run_product_backtest(
        config,
        bars,
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v1",
        corporate_actions=[action],
    )

    assert run.result.dividend_total > 0
    assert "explicit dividend cash flows" in run.report.summary["price_adjustment_model"]


def test_equity_csv_rejects_weekend_execution_bars(tmp_path) -> None:
    csv_path = tmp_path / "weekend.csv"
    csv_path.write_text(
        "timestamp,symbol,timeframe,open,high,low,close,volume,market\n"
        "2025-01-04T00:00:00+00:00,SPY,1d,100,101,99,100,1000,equity\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="weekend"):
        cli_module.load_csv_bars(csv_path, default_market=Market.EQUITY)


def test_download_candidates_use_broad_eligible_universe_not_todays_shortlist(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    candidate_path = tmp_path / "equity-candidates.json"
    candidate_path.write_text(
        json.dumps({"symbols": ["AAA", "BBB", "ZZZ"], "shortlist_symbols": ["ZZZ"]}),
        encoding="utf-8",
    )
    config = replace(
        config,
        universe=replace(
            config.universe,
            equity=replace(
                config.universe.equity,
                candidate_universe_path=str(candidate_path),
            ),
        ),
    )

    broad = ["AAA", "BBB", "ZZZ", "HISTORICAL_LIQUID", "SPY"]
    monkeypatch.setattr(
        "parayabuck.universe.preflight.eligible_equity_symbols",
        lambda _config, trading, limit=None: broad if limit is None else broad[:limit],
    )

    # The historical candidate set is the broad eligible universe, never the
    # current shortlist file: a symbol liquid in 2022 but absent today can
    # still enter the historical Stage-A selection. Scanner-reserved symbols
    # (references, approved inverse ETFs, fallback symbols) are always
    # retained even under an explicit cap, because the point-in-time scanner
    # reserves them in every snapshot and the backtest gate needs their bars.
    # The default _config universe only reserves its reference symbol.
    assert cli_module._candidate_symbols(config, Market.EQUITY, None, None) == [
        "SPY",
        "AAA",
        "BBB",
        "ZZZ",
        "HISTORICAL_LIQUID",
    ]
    assert cli_module._candidate_symbols(config, Market.EQUITY, None, 2) == ["SPY", "AAA"]
    assert cli_module._candidate_symbols(config, Market.EQUITY, "AAPL,MSFT", None) == [
        "AAPL",
        "MSFT",
    ]


def test_load_bars_downloads_warmup_history_before_requested_start(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(cli_module, "ensure_active_universes", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "parayabuck.universe.preflight.eligible_equity_symbols",
        lambda _config, _trading, limit=None: ["SPY", "AAA"],
    )
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
    assert first["symbols"] == ("SPY", "AAA")
    # Empty downloads (no pre-start history) trigger the bounded warmup
    # extension: at most three fetch attempts.
    assert len(captured_requests) == 3
    assert captured_requests[-1]["start"] <= first["start"]


def test_warmup_window_covers_strategy_minimum_history_and_scanner_lookback(
    tmp_path,
) -> None:
    # The SHIPPED DEFAULT strategy (equity_residual_momentum_v2, identical
    # windows to v1) declares minimum_history_bars=274 daily bars — not the
    # reduced-window fixture config used by the other backtest tests.
    base = Config()
    config = replace(
        base,
        runtime=replace(base.runtime, decision_db_path=str(tmp_path / "runtime.sqlite3")),
    )
    # The warmup window counts REAL NYSE sessions (holiday-aware), so it
    # spans more calendar days than bars: at least the no-holiday Mon-Fri
    # minimum (ceil(274*7/5)=384) and bounded by a plausible holiday count.
    days = cli_module._warmup_window_days(config, Market.EQUITY)
    assert 384 <= days <= 460
    assert days >= config.universe.equity.scanner.minimum_data_completeness  # sanity


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


def test_reserved_symbols_without_bars_do_not_block_periods(tmp_path) -> None:
    """A scanner-reserved symbol (reference/inverse/fallback) that has no bars
    in a period must be dropped from that snapshot instead of making the
    decision gate fail-closed for the whole run."""
    from parayabuck.backtest.universe import build_trailing_point_in_time_universe

    config = _config(tmp_path)
    bars = _bars(days=40)
    # Configure an inverse symbol that the scanner reserves but that has no
    # bars in the supplied set.
    config = replace(
        config,
        universe=replace(
            config.universe,
            equity=replace(
                config.universe.equity,
                inverse_trade_symbols=("SH",),
                fallback_trade_symbols=("AAPL", "SH"),
            ),
        ),
    )
    assert "SH" not in {bar.symbol for bar in bars}
    universe = build_trailing_point_in_time_universe(
        bars,
        market=Market.EQUITY,
        maximum_symbols=config.universe.equity.maximum_stream_symbols,
        rebalance_frequency=config.backtest.scanner_rebalance,
        lookback_days=20,
        minimum_history_ratio=config.universe.equity.scanner.minimum_data_completeness,
        minimum_price=config.universe.equity.scanner.minimum_price,
        minimum_dollar_volume=config.universe.equity.scanner.minimum_median_dollar_volume,
        pinned_symbols=config.universe.equity.reference_symbols,
        target_shortlist=config.universe.equity.target_shortlist,
        equity_config=config.universe.equity,
    )
    assert universe.snapshots
    for snapshot in universe.snapshots:
        # The bar-less reserved symbol never appears in a snapshot.
        assert "SH" not in snapshot.symbols
        assert all(symbol in {bar.symbol for bar in bars} for symbol in snapshot.symbols)
