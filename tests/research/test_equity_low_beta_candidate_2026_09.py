"""Synthetic contract tests for the C2 low-beta research candidate.

These tests deliberately use no repository market data.  The candidate module
is imported lazily while its builder seam is being added; once present, the
tests exercise only deterministic beta/selection helpers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from dataclasses import replace
import json

import pytest

from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot
from parayabuck.config import Config
from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.base import PortfolioContext


candidate = pytest.importorskip(
    "scripts.research.equity_low_beta_candidate_2026_09",
    reason="C2 builder module has not landed yet",
)


def _fn(*names: str):  # type: ignore[no-untyped-def]
    for name in names:
        function = getattr(candidate, name, None)
        if callable(function):
            return function
    pytest.skip(f"C2 public seam not exposed yet: {names}")


def _prices(beta: float, market_returns: list[float]) -> tuple[list[tuple[datetime, float]], dict[datetime, float]]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    market = 100.0
    stock = 100.0
    stock_rows: list[tuple[datetime, float]] = [(start, stock)]
    benchmark = {start: market}
    for index, market_return in enumerate(market_returns, start=1):
        market *= 1.0 + market_return
        stock *= 1.0 + beta * market_return
        timestamp = start + timedelta(days=index)
        benchmark[timestamp] = market
        stock_rows.append((timestamp, stock))
    return stock_rows, benchmark


def test_beta_oracle_orders_two_x_before_half_x() -> None:
    compute = _fn("aligned_beta", "compute_beta", "beta_from_returns")
    spy = [(-1) ** i * (i + 1) / 10_000 for i in range(252)]
    high_prices, benchmark = _prices(2.0, spy)
    low_prices, _ = _prices(0.5, spy)
    high = float(compute(high_prices, benchmark))
    low = float(compute(low_prices, benchmark))
    assert high == pytest.approx(2.0, abs=1e-10)
    assert low == pytest.approx(0.5, abs=1e-10)


def test_beta_uses_only_prior_window_when_future_prices_are_appended() -> None:
    compute = _fn("aligned_beta", "compute_beta", "beta_from_returns")
    spy = [(-1) ** i * (i + 1) / 10_000 for i in range(252)]
    stock, benchmark = _prices(2.0, spy)
    baseline = float(compute(stock, benchmark))
    future = max(benchmark) + timedelta(days=1)
    mutated_benchmark = dict(benchmark)
    mutated_benchmark[future] = 999.0
    mutated = float(compute(stock, mutated_benchmark))
    assert mutated == pytest.approx(baseline, abs=1e-10)


def test_flat_spy_variance_is_rejected() -> None:
    compute = _fn("aligned_beta", "compute_beta", "beta_from_returns")
    stock, benchmark = _prices(1.0, [0.0] * 252)
    assert compute(stock, benchmark) is None


def test_price_history_boundary_requires_253_prices_for_252_returns() -> None:
    minimum = getattr(candidate, "MINIMUM_HISTORY_BARS", 253)
    assert minimum == 253
    compute = _fn("aligned_beta", "compute_beta", "beta_from_returns")
    stock, benchmark = _prices(2.0, [(-1) ** i * 0.001 for i in range(252)])
    assert compute(stock, benchmark) is not None
    assert compute(stock[:-1], dict(list(benchmark.items())[:-1])) is None


@pytest.mark.parametrize(
    "mutator",
    [
        lambda rows: tuple(reversed(rows)),
        lambda rows: tuple(rows) + (rows[-1],),
        lambda rows: tuple(rows[:-1]) + ((rows[-1][0], float("nan")),),
    ],
    ids=("unsorted", "duplicate", "nonfinite"),
)
def test_invalid_timestamp_or_price_history_is_rejected(mutator) -> None:  # type: ignore[no-untyped-def]
    compute = _fn("aligned_beta")
    stock, benchmark = _prices(2.0, [(-1) ** i * 0.001 for i in range(252)])
    assert compute(mutator(stock), benchmark) is None


def test_selection_excludes_spy_and_inverse_symbols() -> None:
    select = _fn("rank_low_beta")
    symbols = ("SPY", "SH", "PSQ", "DOG", "AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
    start = datetime(2025, 1, 1, tzinfo=UTC)
    histories = {symbol: [(start + timedelta(days=i), 100.0 + i) for i in range(253)] for symbol in symbols}
    benchmark = {start + timedelta(days=i): 100.0 + i for i in range(253)}
    selected = tuple(item.symbol for item in select(histories, benchmark, symbols))
    assert not set(selected).intersection({"SPY", "SH", "PSQ", "DOG"})


def test_ranking_is_as_of_invariant_when_future_stock_and_benchmark_bars_are_added() -> None:
    rank = _fn("rank_low_beta")
    start = datetime(2025, 1, 1, tzinfo=UTC)
    market_returns = [(-1) ** i * 0.001 for i in range(252)]
    histories = {}
    benchmark: dict[datetime, float] = {}
    for symbol, beta in (("A", 0.5), ("B", 1.0), ("C", 2.0), ("D", 0.8), ("E", 1.2), ("F", 1.5)):
        rows, series = _prices(beta, market_returns)
        histories[symbol] = rows
        benchmark.update(series)
    as_of = start + timedelta(days=252)
    baseline = tuple(item.symbol for item in rank(histories, benchmark, histories, as_of=as_of))
    future_ts = as_of + timedelta(days=1)
    future_histories = {symbol: rows + [(future_ts, 10_000.0)] for symbol, rows in histories.items()}
    future_benchmark = dict(benchmark)
    future_benchmark[future_ts] = 0.01
    mutated = tuple(item.symbol for item in rank(future_histories, future_benchmark, future_histories, as_of=as_of))
    assert mutated == baseline


def test_selection_requires_six_valid_names() -> None:
    select = _fn("rank_low_beta")
    start = datetime(2025, 1, 1, tzinfo=UTC)
    histories = {symbol: [(start + timedelta(days=i), 100.0 + i) for i in range(253)] for symbol in ("A", "B", "C", "D", "E")}
    benchmark = {start + timedelta(days=i): 100.0 + i for i in range(253)}
    with pytest.raises(ValueError):
        weights = candidate.equal_capped_weights(tuple(item.symbol for item in candidate.rank_low_beta(histories, benchmark, histories)))
        assert weights


def test_six_names_are_equal_weighted_and_respect_caps() -> None:
    selected = ("A", "B", "C", "D", "E", "F")
    weights = candidate.equal_capped_weights(selected)
    values = tuple(weights.values())
    assert len(values) == 6
    assert all(float(value) <= 0.10 + 1e-12 for value in values)
    assert sum(float(value) for value in values) == pytest.approx(0.55)


def _engine_bars(days: int = 310) -> list[Bar]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    symbols = ("SPY", "A", "B", "C", "D", "E", "F")
    bars: list[Bar] = []
    for day in range(days):
        timestamp = start + timedelta(days=day)
        market_return = 0.0005 if day % 2 else -0.0004
        spy_close = 100.0 * (1.0 + 0.0001) ** day * (1.0 + market_return)
        for offset, symbol in enumerate(symbols):
            close = spy_close if symbol == "SPY" else 100.0 * (1.0 + (0.5 + offset * 0.05) * market_return) ** day
            bars.append(Bar(timestamp, symbol, "1d", close * 0.99, close * 1.01, close * 0.98, close, 2_000_000, Market.EQUITY))
    return bars


def test_engine_uses_monthly_decisions_and_next_tradable_open(tmp_path) -> None:
    engine_module = pytest.importorskip("scripts.research.equity_low_beta_engine_2026_09")
    config = replace(Config(), runtime=replace(Config().runtime, decision_db_path=str(tmp_path / "runtime.sqlite3")))
    bars = _engine_bars()
    start = bars[0].timestamp + timedelta(days=253)
    end = bars[-1].timestamp
    snapshot = UniverseSnapshot(start - timedelta(seconds=1), frozenset({"SPY", "A", "B", "C", "D", "E", "F"}), observation_set=("SPY", "A", "B", "C", "D", "E", "F"), maximum_stream_symbols=7)
    universe = PointInTimeUniverse(Market.EQUITY, (snapshot,), "synthetic")
    engine = engine_module.build_engine(config, engine_module.build_candidate_strategy(), universe)
    selected = [bar for bar in bars if start <= bar.timestamp <= end]
    warmup = [bar for bar in bars if bar.timestamp < start]
    result = engine.run(selected, warmup=warmup, liquidate_at_end=False)
    assert result.fills
    assert all(fill.timestamp > fill.decision_timestamp for fill in result.fills)
    assert all(fill.timestamp.day != fill.decision_timestamp.day or fill.timestamp > fill.decision_timestamp for fill in result.fills)


def test_engine_strategy_returns_cash_when_six_valid_names_are_unavailable() -> None:
    engine_module = pytest.importorskip("scripts.research.equity_low_beta_engine_2026_09")
    bars = _engine_bars(253)
    decision = bars[-1].timestamp
    symbols = ("SPY", "A", "B", "C", "D", "E")
    snapshot = UniverseSnapshot(decision - timedelta(seconds=1), frozenset(symbols), observation_set=symbols, maximum_stream_symbols=6)
    universe = PointInTimeUniverse(Market.EQUITY, (snapshot,), "synthetic")
    history = {symbol: tuple(bar for bar in bars if bar.symbol == symbol) for symbol in symbols}
    prices = {symbol: series[-1].close for symbol, series in history.items()}
    context = PortfolioContext(decision, Market.EQUITY, "1d", history, universe, "synthetic", 100_000.0, {}, prices, "SPY")
    target = engine_module.build_candidate_strategy().decide(context)
    assert target.weights == ()
    assert target.cash_weight == pytest.approx(1.0)
    assert target.metadata["risk_off_detail"] == "coverage_insufficient"


def test_product_universe_bootstrap_allows_rank_callback_but_decide_requires_pit_universe() -> None:
    engine_module = pytest.importorskip("scripts.research.equity_low_beta_engine_2026_09")
    bars = _engine_bars()
    strategy = engine_module.build_candidate_strategy()
    universe = engine_module.build_research_universe(Config(), bars, strategy, end=bars[-1].timestamp)
    assert universe.snapshots
    assert all(set(snapshot.observation_set).issubset(snapshot.symbols) for snapshot in universe.snapshots)

    history = {symbol: tuple(bar for bar in bars if bar.symbol == symbol) for symbol in ("SPY", "A", "B", "C", "D", "E", "F")}
    decision = bars[-1].timestamp
    prices = {symbol: series[-1].close for symbol, series in history.items()}
    context = PortfolioContext(decision, Market.EQUITY, "1d", history, None, "missing-pit", 100_000.0, {}, prices, "SPY")
    with pytest.raises(RuntimeError, match="universe guard"):
        strategy.decide(context)


def test_run_case_preserves_canonical_daily_alias_and_reports_result_boundary(tmp_path) -> None:
    engine_module = pytest.importorskip("scripts.research.equity_low_beta_engine_2026_09")
    config = replace(Config(), runtime=replace(Config().runtime, decision_db_path=str(tmp_path / "runtime.sqlite3")))
    strategy = engine_module.build_candidate_strategy()
    assert strategy.timeframe == "1d"
    assert strategy.spec().primary_timeframe == "1d"
    bars = _engine_bars()
    start = bars[0].timestamp + timedelta(days=253)
    end = bars[-1].timestamp
    symbols = frozenset({"SPY", "A", "B", "C", "D", "E", "F"})
    snapshot = UniverseSnapshot(start - timedelta(seconds=1), symbols, observation_set=tuple(sorted(symbols)), maximum_stream_symbols=7)
    universe = PointInTimeUniverse(Market.EQUITY, (snapshot,), "synthetic")
    case = engine_module.run_case(config, bars, universe, strategy, start=start, end=end)
    assert case.result.equity_curve
    assert case.coverage_status == "PASS_NO_COVERAGE_BLOCK"
    assert case.terminal_event_count == 0
    assert case.terminal_map_digest
    assert case.terminal_policy == "T1"
    assert all(fill.timestamp > fill.decision_timestamp for fill in case.result.fills)


def test_candidate_is_monthly_but_current_v3_control_remains_weekly() -> None:
    engine_module = pytest.importorskip("scripts.research.equity_low_beta_engine_2026_09")
    candidate = engine_module.build_candidate_strategy()
    control = engine_module.build_control_strategy(Config())
    liquidity = engine_module.build_liquidity_control_strategy()
    assert candidate.rebalance_policy_declared == "monthly"
    assert liquidity.rebalance_policy_declared == "monthly"
    assert control.rebalance_policy_declared == "weekly"
    assert engine_module.FULL_START == datetime(2017, 2, 1, tzinfo=UTC)
    assert engine_module.case_windows()[0][1] == engine_module.FULL_START


def test_run_case_marks_candidate_coverage_blocked_but_v3_cash_control_not_blocked(tmp_path) -> None:
    engine_module = pytest.importorskip("scripts.research.equity_low_beta_engine_2026_09")
    config = replace(Config(), runtime=replace(Config().runtime, decision_db_path=str(tmp_path / "runtime.sqlite3")))
    bars = _engine_bars()
    start = bars[0].timestamp + timedelta(days=253)
    symbols = frozenset({"SPY", "A", "B", "C", "D", "E"})
    snapshot = UniverseSnapshot(start - timedelta(seconds=1), symbols, observation_set=tuple(sorted(symbols)), maximum_stream_symbols=6)
    universe = PointInTimeUniverse(Market.EQUITY, (snapshot,), "synthetic")
    candidate_case = engine_module.run_case(config, bars, universe, engine_module.build_candidate_strategy(), start=start, end=bars[-1].timestamp)
    control_case = engine_module.run_case(config, bars, universe, engine_module.build_control_strategy(config), start=start, end=bars[-1].timestamp)
    assert candidate_case.coverage_status == "BLOCKED_COVERAGE_INSUFFICIENT"
    assert control_case.coverage_status == "NOT_APPLICABLE_CONTROL"


def test_t1_terminal_event_fires_once_without_end_liquidation() -> None:
    engine_module = pytest.importorskip("scripts.research.equity_low_beta_engine_2026_09")
    from parayabuck.backtest.terminal_aware_engine import TerminalInfo

    config = Config()
    bars = _engine_bars()
    start = bars[0].timestamp + timedelta(days=253)
    snapshot = UniverseSnapshot(start - timedelta(seconds=1), frozenset({"SPY", "A", "B", "C", "D", "E", "F"}), observation_set=("SPY", "A", "B", "C", "D", "E", "F"), maximum_stream_symbols=7)
    universe = PointInTimeUniverse(Market.EQUITY, (snapshot,), "synthetic")
    terminal_ts = bars[-1].timestamp
    terminal_map = {"A": TerminalInfo("entity-A", "A", "A", terminal_ts, "delisted", "synthetic", False)}
    engine = engine_module.build_engine(config, engine_module.build_candidate_strategy(), universe, terminal_map=terminal_map, terminal_policy="T1")
    selected = [bar for bar in bars if start <= bar.timestamp <= terminal_ts]
    warmup = [bar for bar in bars if bar.timestamp < start]
    engine.run(selected, warmup=warmup, liquidate_at_end=False)
    assert sum(event["symbol"] == "A" for event in engine.terminal_events_fired) == 1


def test_postwindow_bar_cannot_change_prior_run_case_result(tmp_path) -> None:
    engine_module = pytest.importorskip("scripts.research.equity_low_beta_engine_2026_09")
    config = replace(Config(), runtime=replace(Config().runtime, decision_db_path=str(tmp_path / "runtime.sqlite3")))
    bars = _engine_bars()
    start = bars[0].timestamp + timedelta(days=253)
    end = bars[-1].timestamp - timedelta(days=1)
    symbols = frozenset({"SPY", "A", "B", "C", "D", "E", "F"})
    snapshot = UniverseSnapshot(start - timedelta(seconds=1), symbols, observation_set=tuple(sorted(symbols)), maximum_stream_symbols=7)
    universe = PointInTimeUniverse(Market.EQUITY, (snapshot,), "synthetic")
    baseline = engine_module.run_case(config, bars, universe, engine_module.build_candidate_strategy(), start=start, end=end)
    future_ts = end + timedelta(days=1)
    extra = [Bar(future_ts, symbol, "1d", 10_000, 10_001, 9_999, 10_000, 2_000_000, Market.EQUITY) for symbol in symbols]
    mutated = engine_module.run_case(config, bars + extra, universe, engine_module.build_candidate_strategy(), start=start, end=end)
    assert baseline.result.total_return == pytest.approx(mutated.result.total_return)
    assert baseline.result.rebalance_count == mutated.result.rebalance_count


def test_unverified_manifest_source_head_is_refused(tmp_path) -> None:
    engine_module = pytest.importorskip("scripts.research.equity_low_beta_engine_2026_09")
    manifest = tmp_path / "manifest.json"
    preflight = tmp_path / "preflight.json"
    manifest.write_text(json.dumps({"status": "FROZEN", "manifest_sha256": "abc", "review": {"frozen": True}, "inputs": {"end": engine_module.FULL_END.isoformat(), "bars": {"sha256": "bars"}}, "source": {"head": "unverified"}}))
    preflight.write_text(json.dumps({"status": "COMPLETE", "observed_sha256": {"bars": "bars"}, "source": {"head": "unverified"}}))
    with pytest.raises(RuntimeError, match="BLOCKED_SOURCE_HEAD_MISMATCH"):
        engine_module.require_frozen_inputs(manifest, preflight)


def test_frozen_verdict_boundaries_are_scalar_and_cost_specific() -> None:
    candidate_module = pytest.importorskip("scripts.research.equity_low_beta_candidate_2026_09")
    base = {
        candidate_module.IDENTITY: {"cagr": 0.106, "max_drawdown": 0.20},
        "monthly_dollar_volume_top6_v1": {"cagr": 0.10, "max_drawdown": 0.21},
        "equity_residual_momentum_v3": {"cagr": 0.12, "max_drawdown": 0.25},
    }
    recent = {name: dict(values) for name, values in base.items()}
    assert candidate_module.evaluate_verdict(base, recent)["status"] == "PASS"
    failed = {name: dict(values) for name, values in base.items()}
    failed[candidate_module.IDENTITY]["cagr"] = 0.079
    assert candidate_module.evaluate_verdict(failed, recent)["status"] == "REJECT"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda values: values.pop("cagr"),
        lambda values: values.__setitem__("cagr", float("nan")),
        lambda values: values.__setitem__("max_drawdown", float("inf")),
    ],
    ids=("missing", "nan", "infinite"),
)
def test_nonfinite_or_missing_gate_metrics_are_blocked(mutator) -> None:  # type: ignore[no-untyped-def]
    candidate_module = pytest.importorskip("scripts.research.equity_low_beta_candidate_2026_09")
    full = {
        candidate_module.IDENTITY: {"cagr": 0.106, "max_drawdown": 0.20},
        "monthly_dollar_volume_top6_v1": {"cagr": 0.10, "max_drawdown": 0.21},
        "equity_residual_momentum_v3": {"cagr": 0.12, "max_drawdown": 0.25},
    }
    recent = {name: dict(values) for name, values in full.items()}
    mutator(full[candidate_module.IDENTITY])
    result = candidate_module.evaluate_verdict(full, recent)
    assert result["status"] == "BLOCKED"


def test_public_economic_runner_uses_selected_temp_receipt_for_all_18_synthetic_cells(monkeypatch, tmp_path) -> None:
    candidate_module = pytest.importorskip("scripts.research.equity_low_beta_candidate_2026_09")
    engine_module = pytest.importorskip("scripts.research.equity_low_beta_engine_2026_09")
    import scripts.run_symmetric_v5_validation as validation_module
    import parayabuck.backtest.terminal_policy as terminal_policy_module
    import parayabuck.backtest.terminal_aware_engine as terminal_engine_module

    bars = _long_synthetic_bars()
    source_files = {
        "scripts/research/equity_low_beta_candidate_2026_09.py": candidate_module.sha256_file(candidate_module.REPO / "scripts/research/equity_low_beta_candidate_2026_09.py"),
        "scripts/research/equity_low_beta_engine_2026_09.py": candidate_module.sha256_file(candidate_module.REPO / "scripts/research/equity_low_beta_engine_2026_09.py"),
    }
    inputs = {
        "bars": {"path": "synthetic-bars.csv", "sha256": "bars"},
        "lifetimes": {"path": "synthetic-lifetimes.csv", "sha256": "lifetimes"},
        "corporate_actions": {"path": "synthetic-actions.csv", "sha256": "actions"},
        "end": engine_module.FULL_END.isoformat(),
    }
    manifest = {
        "status": "FROZEN",
        "manifest_sha256": "synthetic-r3-manifest",
        "review": {"frozen": True},
        "inputs": inputs,
        "evaluation": {
            "full": {"start": engine_module.FULL_START.isoformat(), "end": engine_module.FULL_END.isoformat()},
            "recent_24_months": {"start": engine_module.RECENT_START.isoformat(), "end": engine_module.FULL_END.isoformat()},
        },
        "source": {"head": engine_module.SOURCE_HEAD, "file_sha256": source_files},
    }
    receipt = {
        "status": "COMPLETE",
        "observed_sha256": {"bars": "bars", "lifetimes": "lifetimes", "corporate_actions": "actions"},
        "source": {"head": engine_module.SOURCE_HEAD},
    }
    manifest_path = tmp_path / "r3_manifest.json"
    receipt_path = tmp_path / "r3_receipt.json"
    manifest_path.write_text(json.dumps(manifest))
    receipt_path.write_text(json.dumps(receipt))
    monkeypatch.setattr(candidate_module, "preflight", lambda: {"observed_sha256": receipt["observed_sha256"]})
    monkeypatch.setattr(validation_module, "load_bars", lambda _path: bars)
    monkeypatch.setattr(validation_module, "load_corporate_actions_all", lambda: [])
    monkeypatch.setattr(terminal_policy_module, "classify_terminal_events", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(terminal_engine_module, "build_terminal_map", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(candidate_module, "DATASET", tmp_path / "synthetic-bars.csv")
    monkeypatch.setattr(candidate_module, "LIFETIMES", tmp_path / "synthetic-lifetimes.csv")
    monkeypatch.setattr(candidate_module, "CORPORATE_ACTIONS", tmp_path / "synthetic-actions.csv")
    monkeypatch.setattr(candidate_module, "SETTINGS", candidate_module.REPO / "config/settings.yaml")
    synthetic_symbols = frozenset({"SPY", "A", "B", "C", "D", "E", "F"})
    synthetic_universe = PointInTimeUniverse(
        Market.EQUITY,
        (UniverseSnapshot(datetime(2016, 1, 1, tzinfo=UTC), synthetic_symbols, observation_set=tuple(sorted(synthetic_symbols)), maximum_stream_symbols=7),),
        "synthetic-frozen-pit",
    )
    monkeypatch.setattr(engine_module, "build_research_universe", lambda *_args, **_kwargs: synthetic_universe)
    output_dir = tmp_path / "results"
    candidate_module.run_economic(manifest_path, preflight_path=receipt_path, output_dir=output_dir)
    result = json.loads((output_dir / "economic_results.json").read_text())
    assert result["status"] in {"COMPLETE", "BLOCKED"}
    assert result["status"] == "COMPLETE"
    assert len(result["records"]) == 18
    assert len({(row["window"], row["case"], row["cost_multiplier"]) for row in result["records"]}) == 18


def _long_synthetic_bars() -> list[Bar]:
    start = datetime(2016, 1, 1, tzinfo=UTC)
    end = datetime(2026, 7, 30, tzinfo=UTC)
    symbols = ("SPY", "A", "B", "C", "D", "E", "F")
    bars: list[Bar] = []
    day = 0
    cursor = start
    while cursor <= end:
        market_return = 0.0003 if day % 3 else -0.0002
        spy_close = 100.0 * (1.0 + 0.00002) ** day * (1.0 + market_return)
        for offset, symbol in enumerate(symbols):
            close = spy_close if symbol == "SPY" else 100.0 * (1.0 + (0.6 + offset * 0.05) * market_return) ** day
            bars.append(Bar(cursor, symbol, "1d", close * 0.99, close * 1.01, close * 0.98, close, 2_000_000, Market.EQUITY))
        day += 1
        cursor += timedelta(days=1)
    return bars


def test_liquidity_control_uses_same_excluded_pool_and_is_deterministic() -> None:
    candidate_module = pytest.importorskip("scripts.research.equity_low_beta_candidate_2026_09")
    start = datetime(2025, 1, 1, tzinfo=UTC)
    histories = {
        symbol: [(start + timedelta(days=i), 100.0 + i, float(1000 + offset)) for i in range(20)]
        for offset, symbol in enumerate(("SPY", "SH", "PSQ", "DOG", "A", "B", "C", "D", "E", "F"))
    }
    ranked = candidate_module.rank_dollar_volume(histories, histories)
    assert not set(ranked).intersection({"SPY", "SH", "PSQ", "DOG"})
    assert ranked == ("F", "E", "D", "C", "B", "A")
