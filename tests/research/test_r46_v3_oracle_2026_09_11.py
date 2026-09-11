from __future__ import annotations

import ast
import math
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import scripts.research.r46_v3_oracle_2026_09_11 as oracle_module
from scripts.research.r46_v3_oracle_2026_09_11 import (
    FROZEN_ACCOUNT_CAPS,
    CachedV3TargetOracle,
    OracleTarget,
    OracleWeight,
    apply_account_risk,
    compute_v3_target,
    effective_observation_symbols,
    inverse_volatility_weights,
    multi_horizon_consensus,
    ols_beta,
    percentile_ranks,
    reject_quantile_threshold,
)

ROOT = Path(__file__).resolve().parents[2]
ORACLE_PATH = ROOT / "scripts/research/r46_v3_oracle_2026_09_11.py"
UTC_START = datetime(2026, 1, 2, tzinfo=UTC)


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: datetime
    close: float


@dataclass(frozen=True)
class Snapshot:
    effective_at: datetime
    symbols: frozenset[str]
    observation_set: tuple[str, ...]
    maximum_stream_symbols: int
    pinned_symbols: tuple[str, ...]


def _fixture_bars() -> tuple[Bar, ...]:
    prices = {symbol: 100.0 for symbol in ("SPY", "AAA", "BBB", "CCC", "DDD", "TLT")}
    betas = {"AAA": 0.8, "BBB": 1.1, "CCC": 0.5, "DDD": 1.3, "TLT": 0.2}
    drifts = {"AAA": 0.00080, "BBB": 0.00045, "CCC": 0.00025, "DDD": 0.00018, "TLT": 0.00010}
    rows: list[Bar] = []
    for index in range(274):
        timestamp = UTC_START + timedelta(days=index)
        market_return = 0.0008 + 0.0002 * ((index % 5) - 2)
        spy_return = market_return
        prices["SPY"] *= 1.0 + spy_return
        rows.append(Bar("SPY", timestamp, prices["SPY"]))
        for symbol, beta in betas.items():
            noise = 0.00003 * (((index + len(symbol)) % 7) - 3)
            extra_noise = 0.0015 * (((index * 11) % 9) - 4) if symbol == "DDD" else noise
            prices[symbol] *= 1.0 + beta * market_return + drifts[symbol] + extra_noise
            rows.append(Bar(symbol, timestamp, prices[symbol]))
    return tuple(rows)


def test_pure_math_boundaries_are_exact_and_tie_averaged() -> None:
    ranks = percentile_ranks({"a": 1.0, "b": 1.0, "c": 2.0})
    assert ranks == {"a": pytest.approx(1.0 / 3.0), "b": pytest.approx(1.0 / 3.0), "c": pytest.approx(5.0 / 6.0)}
    assert percentile_ranks({"a": 1.0, "b": 1.0, "c": 2.0, "d": 3.0}) == {
        "a": pytest.approx(0.25),
        "b": pytest.approx(0.25),
        "c": pytest.approx(0.625),
        "d": pytest.approx(0.875),
    }
    assert reject_quantile_threshold(list(range(1, 11)), 0.70) == 7
    assert reject_quantile_threshold([1, 2, 3, 4, 5], 0.70) == 3
    assert reject_quantile_threshold([1.0, 1.0, 2.0], 0.70) == 1.0
    assert inverse_volatility_weights(("a", "b", "c"), {"a": 1.0, "b": 2.0, "c": 4.0}, 0.40) == {
        "a": pytest.approx(0.40),
        "b": pytest.approx(2.0 / 7.0),
        "c": pytest.approx(1.0 / 7.0),
    }
    assert ols_beta([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)
    assert math.isfinite(multi_horizon_consensus({"a": [0.01] * 273, "b": [0.02] * 273}, (126, 189, 252), 21)["a"])


def test_skip_tail_and_six_position_account_scaling_are_frozen() -> None:
    base = [0.001] * 273
    tail_changed = [*base[:-21], *([0.25] * 21)]
    assert multi_horizon_consensus(
        {"a": base, "b": [0.002] * 273}, (126, 189, 252), 21
    ) == multi_horizon_consensus(
        {"a": tail_changed, "b": [0.002] * 273}, (126, 189, 252), 21
    )
    target = OracleTarget(
        decision_timestamp=UTC_START,
        observation_symbols=tuple(f"S{index}" for index in range(8)),
        ranked_symbols=tuple(f"S{index}" for index in range(8)),
        qualified_symbols=tuple(f"S{index}" for index in range(8)),
        scores=(),
        residual_volatilities=(),
        weights=tuple(OracleWeight(f"S{index}", 0.10, index + 1) for index in range(8)),
        cash_weight=0.20,
    )
    risk_target = apply_account_risk(
        target,
        replay_state={"holdings": {}, "marks": {}, "equity": 1.0},
        approved_symbols=target.observation_symbols,
        maximum_allocation=0.55,
        caps=FROZEN_ACCOUNT_CAPS,
    )
    assert len(risk_target.weights) == 6
    assert all(item.weight == pytest.approx(0.55 / 6.0) for item in risk_target.weights)
    assert risk_target.cash_weight == pytest.approx(0.45)


def test_account_risk_keeps_the_six_lowest_oracle_ranks_not_alphabetical_symbols() -> None:
    symbols_by_rank = ("ZZ", "YY", "XX", "WW", "VV", "UU", "TT", "SS")
    target = OracleTarget(
        decision_timestamp=UTC_START,
        observation_symbols=symbols_by_rank,
        ranked_symbols=symbols_by_rank,
        qualified_symbols=symbols_by_rank,
        scores=(),
        residual_volatilities=(),
        weights=tuple(OracleWeight(symbol, 0.10, rank) for rank, symbol in enumerate(symbols_by_rank, 1)),
        cash_weight=0.20,
    )
    result = apply_account_risk(
        target,
        replay_state={"holdings": {}, "marks": {}, "equity": 1.0},
        approved_symbols=symbols_by_rank,
        maximum_allocation=0.55,
        caps=FROZEN_ACCOUNT_CAPS,
    )
    assert tuple(item.symbol for item in result.weights) == tuple(sorted(symbols_by_rank[:6]))
    assert {item.symbol: item.rank for item in result.weights} == {
        symbol: rank for rank, symbol in enumerate(symbols_by_rank[:6], 1)
    }


def test_pending_order_ledger_reconstructs_partial_remainder_from_exact_fill_id() -> None:
    first = UTC_START
    second = first + timedelta(hours=12)
    fill_completion = first + timedelta(days=1)
    third = first + timedelta(days=2)
    order_id = "incumbent-v3|v3|2026-01-02T00:00:00+00:00|AAA|buy|1"
    ledger_type = getattr(oracle_module, "PendingOrderLedger", None)
    assert ledger_type is not None, "pending-order oracle is not implemented"
    ledger = ledger_type(
        fills=(
            {
                "client_order_id": order_id,
                "decision_timestamp": first,
                "timestamp": first + timedelta(hours=1),
                "symbol": "AAA",
                "side": "BUY",
                "qty": 2.0,
            },
            {
                "client_order_id": order_id,
                "decision_timestamp": first,
                "timestamp": fill_completion,
                "symbol": "AAA",
                "side": "BUY",
                "qty": 3.0,
            },
        ),
        candidate=False,
        event_boundaries=(),
    )
    assert ledger.pending_symbols(second) == ()
    ledger.record_decision(
        first,
        target_weights={"AAA": 0.50},
        pretrade_quantities={},
        marks={"AAA": 100.0},
        equity=1_000.0,
        order_id_by_request={("AAA", "BUY"): order_id},
    )
    assert ledger.pending_symbols(second) == ("AAA",)
    assert ledger.pending_symbols(third) == ()


def test_pending_order_ledger_tracks_tlt_event_remainder_then_expires_at_exact_open() -> None:
    decision = UTC_START
    exact_open = decision + timedelta(days=1)
    after_open = exact_open + timedelta(seconds=1)
    order_id = "r46_treasury_auction_unwind_tlt_v2_2026_09_10|2.0.0|2026-01-02T00:00:00+00:00|TLT|buy|1"
    ledger = oracle_module.PendingOrderLedger(
        fills=(
            {
                "client_order_id": order_id,
                "decision_timestamp": decision,
                "timestamp": exact_open,
                "symbol": "TLT",
                "side": "BUY",
                "qty": 1.0,
            },
        ),
        candidate=True,
        event_boundaries=(decision,),
        expiry_by_request={(decision, "TLT", "BUY"): exact_open},
    )
    ledger.record_decision(
        decision,
        target_weights={"TLT": 0.025},
        pretrade_quantities={},
        marks={"TLT": 100.0},
        equity=10_000.0,
        order_id_by_request={("TLT", "BUY"): order_id},
    )
    assert ledger.pending_symbols(decision) == ("TLT",)
    assert ledger.pending_symbols(exact_open) == ("TLT",)
    assert ledger.pending_symbols(after_open) == ()


def test_pending_order_ledger_preserves_tlt_and_replaces_incumbent_pending_at_event_boundary() -> None:
    ordinary = UTC_START
    event = ordinary + timedelta(hours=1)
    open_timestamp = event + timedelta(days=1)
    tlt_id = "r46_treasury_auction_unwind_tlt_v2_2026_09_10|2.0.0|2026-01-02T01:00:00+00:00|TLT|buy|2"
    aaa_id = "equity_residual_momentum_v3|3.0.0|2026-01-02T00:00:00+00:00|AAA|buy|1"
    ledger = oracle_module.PendingOrderLedger(
        fills=(),
        candidate=True,
        event_boundaries=(event,),
        expiry_by_request={(event, "TLT", "BUY"): open_timestamp},
    )
    ledger.record_decision(
        ordinary,
        target_weights={"AAA": 0.50},
        pretrade_quantities={},
        marks={"AAA": 100.0},
        equity=1_000.0,
        order_id_by_request={("AAA", "BUY"): aaa_id},
    )
    assert ledger.pending_symbols(ordinary) == ("AAA",)
    ledger.record_decision(
        event,
        target_weights={"TLT": 0.025},
        pretrade_quantities={},
        marks={"TLT": 100.0},
        equity=10_000.0,
        order_id_by_request={("TLT", "BUY"): tlt_id},
    )
    assert ledger.pending_symbols(event) == ("AAA", "TLT")


def test_effective_membership_reserves_tlt_pending_in_capacity_three_context() -> None:
    timestamp = UTC_START + timedelta(days=5)
    snapshots = (
        Snapshot(
            effective_at=UTC_START,
            symbols=frozenset({"AAA", "BBB", "CCC", "TLT"}),
            observation_set=("BBB", "CCC"),
            maximum_stream_symbols=3,
            pinned_symbols=(),
        ),
    )
    assert effective_observation_symbols(
        universe=snapshots,
        decision_timestamp=timestamp,
        managed_symbols=("AAA",),
        pending_symbols=("TLT",),
    ) == ("AAA", "TLT", "BBB")


def test_account_risk_enforces_daily_loss_and_high_water_drawdown_state() -> None:
    target = OracleTarget(
        decision_timestamp=UTC_START,
        observation_symbols=("AAA",),
        ranked_symbols=("AAA",),
        qualified_symbols=("AAA",),
        scores=(),
        residual_volatilities=(),
        weights=(OracleWeight("AAA", 0.10, 1),),
        cash_weight=0.90,
    )
    daily_loss = apply_account_risk(
        target,
        replay_state={
            "holdings": {},
            "marks": {},
            "equity": 100_000.0,
            "daily_pnl": -600.0,
            "peak_equity": 100_000.0,
            "enforce_drawdown_halt": False,
        },
        approved_symbols=("AAA",),
        maximum_allocation=0.55,
        caps=FROZEN_ACCOUNT_CAPS,
    )
    assert daily_loss.weights == ()
    assert daily_loss.cash_weight == pytest.approx(1.0)

    drawdown_target = OracleTarget(
        decision_timestamp=UTC_START,
        observation_symbols=("AAA",),
        ranked_symbols=("AAA",),
        qualified_symbols=("AAA",),
        scores=(),
        residual_volatilities=(),
        weights=(OracleWeight("AAA", 0.10, 1),),
        cash_weight=0.90,
    )
    state = {
        "holdings": {"AAA": {"qty": 0.05}},
        "marks": {"AAA": 100.0},
        "equity": 100.0,
        "daily_pnl": 0.0,
        "peak_equity": 200.0,
    }
    shadow = apply_account_risk(
        drawdown_target,
        replay_state={**state, "enforce_drawdown_halt": False},
        approved_symbols=("AAA",),
        maximum_allocation=0.55,
        caps=FROZEN_ACCOUNT_CAPS,
    )
    halted = apply_account_risk(
        drawdown_target,
        replay_state={**state, "enforce_drawdown_halt": True},
        approved_symbols=("AAA",),
        maximum_allocation=0.55,
        caps=FROZEN_ACCOUNT_CAPS,
    )
    assert shadow.weight_map["AAA"] == pytest.approx(0.10)
    assert halted.weight_map["AAA"] == pytest.approx(0.05)


def test_account_risk_full_approved_tlt_context_matches_six_name_scaling() -> None:
    names = tuple(f"S{index}" for index in range(6))
    target = OracleTarget(
        decision_timestamp=UTC_START,
        observation_symbols=(*names, "TLT"),
        ranked_symbols=names,
        qualified_symbols=(*names, "TLT"),
        scores=(),
        residual_volatilities=(),
        weights=tuple(OracleWeight(symbol, 0.10, index + 1) for index, symbol in enumerate(names))
        + (OracleWeight("TLT", 0.025, 0),),
        cash_weight=0.375,
    )
    state = {
        "holdings": {"TLT": {"qty": 0.25}},
        "marks": {"TLT": 100.0},
        "equity": 1_000.0,
        "daily_pnl": 0.0,
        "peak_equity": 1_000.0,
        "enforce_drawdown_halt": False,
    }
    full = apply_account_risk(
        target,
        replay_state=state,
        approved_symbols=(*names, "TLT"),
        maximum_allocation=0.55,
        caps=FROZEN_ACCOUNT_CAPS,
    )
    stripped = apply_account_risk(
        target,
        replay_state=state,
        approved_symbols=names,
        maximum_allocation=0.55,
        caps=FROZEN_ACCOUNT_CAPS,
    )
    assert "TLT" not in full.weight_map
    assert len(full.weights) == 6
    assert all(item.weight == pytest.approx(0.55 / 6.0) for item in full.weights)
    assert stripped.weight_map["TLT"] == pytest.approx(0.025)
    assert len(stripped.weights) == 6
    assert sum(symbol != "TLT" for symbol in stripped.weight_map) == 5


def test_target_reproduces_v3_pipeline_and_strips_tlt_at_context_seam() -> None:
    bars = _fixture_bars()
    timestamp = max(bar.timestamp for bar in bars)
    target = compute_v3_target(
        bars=bars,
        decision_timestamp=timestamp,
        observation_symbols=("SPY", "AAA", "BBB", "CCC", "DDD", "TLT"),
    )
    assert target.observation_symbols == ("SPY", "AAA", "BBB", "CCC", "DDD", "TLT")
    assert "SPY" not in target.ranked_symbols
    assert "TLT" not in target.ranked_symbols
    assert len(target.ranked_symbols) == 3
    assert tuple(weight.symbol for weight in target.weights) == tuple(sorted(weight.symbol for weight in target.weights))
    assert all(weight.rank == target.ranked_symbols.index(weight.symbol) + 1 for weight in target.weights)
    assert all(0.0 < weight.weight <= 0.10 for weight in target.weights)
    assert target.cash_weight == pytest.approx(1.0 - sum(weight.weight for weight in target.weights))


def test_effective_membership_reserves_managed_and_pending_capacity_and_honors_terminal_lifetime() -> None:
    timestamp = UTC_START + timedelta(days=10)
    snapshots = (
        Snapshot(
            effective_at=UTC_START,
            symbols=frozenset({"AAA", "BBB", "CCC", "DDD", "TLT"}),
            observation_set=("AAA", "BBB", "CCC"),
            maximum_stream_symbols=3,
            pinned_symbols=("SPY",),
        ),
    )
    result = effective_observation_symbols(
        universe=snapshots,
        decision_timestamp=timestamp,
        managed_symbols=("BBB",),
        pending_symbols=("CCC",),
        terminal_map={"BBB": {"last_bar": timestamp - timedelta(days=1)}},
    )
    assert result == ("SPY", "CCC", "AAA")


def test_event_boundary_reuses_cached_ordinary_target_even_if_input_changes() -> None:
    bars = _fixture_bars()
    ordinary_timestamp = max(bar.timestamp for bar in bars)
    session = CachedV3TargetOracle(bars=bars, observation_symbols=("SPY", "AAA", "BBB", "CCC", "DDD", "TLT"))
    ordinary = session.target(ordinary_timestamp)
    changed = tuple(
        Bar(bar.symbol, bar.timestamp, bar.close * (1.0 + (0.25 if bar.symbol == "AAA" else 0.0))) for bar in bars
    )
    event = session.target(ordinary_timestamp + timedelta(days=1), event_boundary=True, bars=changed)
    assert event.decision_timestamp == ordinary_timestamp + timedelta(days=1)
    assert event.ranked_symbols == ordinary.ranked_symbols
    assert event.weights == ordinary.weights


def test_oracle_has_no_production_strategy_or_engine_imports_and_fresh_import_works() -> None:
    tree = ast.parse(ORACLE_PATH.read_text(encoding="utf-8"))
    imported = {
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    imported.update(
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert imported <= {"__future__", "ast", "bisect", "collections", "dataclasses", "datetime", "math", "statistics", "typing"}
    forbidden = {
        "EquityResidualMomentum1dV3",
        "PortfolioBacktestEngine",
        "TerminalAwareResearchEngine",
        "TargetPortfolio",
        "PortfolioRiskOverlay",
        "run_symmetric_v5_validation",
    }
    assert not forbidden.intersection(node.id for node in ast.walk(tree) if isinstance(node, ast.Name))
    result = subprocess.run(
        [sys.executable, "-c", "import scripts.research.r46_v3_oracle_2026_09_11 as oracle; print(oracle.__all__[0])"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
