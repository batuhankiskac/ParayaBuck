from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from parayabuck.backtest.holdout_registry import (
    STATE_COMPLETE,
    STATE_FAILED,
    HoldoutFingerprint,
    HoldoutLockedError,
    HoldoutRegistry,
)
from parayabuck.core.models import Market


def _fingerprint(bar_hash: str = "abc123") -> HoldoutFingerprint:
    return HoldoutFingerprint(
        market=Market.EQUITY,
        timeframe="1d",
        universe_snapshot_hash="universe-hash",
        holdout_start=datetime(2026, 2, 1, tzinfo=UTC),
        holdout_end=datetime(2026, 8, 1, tzinfo=UTC),
        adjustment="all",
        source_provider="stooq",
        bar_hash=bar_hash,
    )


def test_preflight_passes_for_fresh_holdout(tmp_path: Path) -> None:
    registry = HoldoutRegistry(tmp_path / "registry.json")
    registry.preflight(_fingerprint(), "experiment-a")


def test_pending_locks_the_holdout(tmp_path: Path) -> None:
    registry = HoldoutRegistry(tmp_path / "registry.json")
    registry.begin_evaluation(_fingerprint(), "experiment-a")
    with pytest.raises(HoldoutLockedError, match="already used"):
        registry.preflight(_fingerprint(), "experiment-a")
    with pytest.raises(HoldoutLockedError):
        registry.begin_evaluation(_fingerprint(), "experiment-b")


def test_completed_holdout_cannot_be_reopened_by_another_experiment(tmp_path: Path) -> None:
    registry = HoldoutRegistry(tmp_path / "registry.json")
    registry.begin_evaluation(_fingerprint(), "experiment-a")
    registry.complete_evaluation(_fingerprint(), "experiment-a", data_hash="data-hash")
    with pytest.raises(HoldoutLockedError, match="experiment-a"):
        registry.preflight(_fingerprint(), "experiment-b")
    records = registry.records_for(_fingerprint().structural_digest())
    assert records[0].state == STATE_COMPLETE


def test_failed_evaluation_can_be_retried(tmp_path: Path) -> None:
    registry = HoldoutRegistry(tmp_path / "registry.json")
    registry.begin_evaluation(_fingerprint(), "experiment-a")
    registry.fail_evaluation(_fingerprint(), "experiment-a", reason="bars missing")
    records = registry.records_for(_fingerprint().structural_digest())
    assert records[0].state == STATE_FAILED
    # A failed evaluation does not lock the holdout permanently.
    registry.begin_evaluation(_fingerprint(), "experiment-a")


def test_fingerprint_bar_hash_only_affects_full_digest(tmp_path: Path) -> None:
    registry = HoldoutRegistry(tmp_path / "registry.json")
    with_bar = _fingerprint(bar_hash="different-bars")
    assert with_bar.structural_digest() == _fingerprint().structural_digest()
    assert with_bar.full_digest() != _fingerprint().full_digest()
    # Same structural holdout -> already locked, even with different bars.
    registry.begin_evaluation(_fingerprint(), "experiment-a")
    registry.complete_evaluation(_fingerprint(), "experiment-a", "h")
    with pytest.raises(HoldoutLockedError):
        registry.preflight(with_bar, "experiment-c")


def test_registry_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    HoldoutRegistry(path).begin_evaluation(_fingerprint(), "experiment-a")
    reloaded = HoldoutRegistry(path)
    with pytest.raises(HoldoutLockedError):
        reloaded.preflight(_fingerprint(), "experiment-b")


def test_complete_requires_pending_record(tmp_path: Path) -> None:
    registry = HoldoutRegistry(tmp_path / "registry.json")
    with pytest.raises(HoldoutLockedError, match="begin_evaluation"):
        registry.complete_evaluation(_fingerprint(), "experiment-a", "h")


def test_registry_locks_holdout_across_experiment_runs(tmp_path: Path) -> None:
    """End-to-end: unlocking a holdout records a complete registry entry and a
    second experiment with the same structural fingerprint is rejected."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from datetime import timedelta

    from parayabuck.backtest.experiments import (
        EQUITY_BASE,
        EQUITY_STRESS,
        CostScenario,
        outer_holdout_start,
    )
    from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
    from parayabuck.backtest.portfolio_experiments import (
        PortfolioCandidateSpace,
        PortfolioExperimentSpec,
        run_portfolio_experiment,
    )
    from parayabuck.core.models import Bar
    from parayabuck.portfolio import rebalance_policy_for
    from parayabuck.portfolio.base import PortfolioStrategy

    start = datetime(2023, 1, 1, tzinfo=UTC)
    end = datetime(2025, 7, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for index in range(950):  # cover the 2025-01..2025-07 holdout window
        timestamp = start + timedelta(days=index)
        for symbol, drift in (("SPY", 0.0004), ("QQQ", 0.0006)):
            close = 100 * (1 + drift) ** index
            bars.append(
                Bar(
                    timestamp,
                    symbol,
                    "1d",
                    close * 0.999,
                    close * 1.001,
                    close * 0.998,
                    close,
                    5_000_000,
                    Market.EQUITY,
                )
            )

    def spec(experiment_id: str) -> PortfolioExperimentSpec:
        return PortfolioExperimentSpec(
            experiment_id=experiment_id,
            start=start,
            end=end,
            universe=("SPY", "QQQ"),
            universe_version="permanent_etf_core_v1",
            candidate_spaces=(
                PortfolioCandidateSpace(
                    "equity_etf_dual_momentum_v1",
                    "1.0.0",
                    Market.EQUITY,
                    "1d",
                    (("momentum_scheme", ("12_1", "6_1")), ("volatility_window", (20, 60))),
                ),
            ),
            train_months=9,
            validation_months=1,
            outer_holdout_months=6,
            embargo_bars=5,
            max_mechanism_trials=30,
            max_parameter_trials=4,
            max_holdout_finalists=1,
            cost_scenarios=(EQUITY_BASE, EQUITY_STRESS),
            source_provider="alpaca",
            adjustment_policy="all",
        )

    def factory(strategy: PortfolioStrategy, cost: CostScenario) -> PortfolioBacktestEngine:
        return PortfolioBacktestEngine(
            strategy,
            rebalance_policy_for(strategy),
            starting_equity=100_000.0,
            slippage_bps=cost.slippage_bps,
            spread_bps=cost.spread_bps,
            benchmark_symbol="SPY",
        )

    registry_path = tmp_path / "holdout_registry.json"
    registry = HoldoutRegistry(registry_path)
    first = run_portfolio_experiment(
        bars,
        spec("experiment-a"),
        factory,
        unlock_holdout=True,
        holdout_registry=registry,
    )
    finalist = next(t for t in first if t.holdout_evaluations == 1)
    assert not finalist.holdout_missing
    assert finalist.holdout_result is not None
    assert finalist.holdout_gate_decision is not None
    actual_fingerprint = HoldoutFingerprint(
        market=Market.EQUITY,
        timeframe="1d",
        universe_snapshot_hash="",
        holdout_start=outer_holdout_start(end, 6),
        holdout_end=end,
        adjustment="all",
        source_provider="alpaca",
    )
    records = registry.records_for(actual_fingerprint.structural_digest())
    assert len(records) == 1
    assert records[0].state == STATE_COMPLETE
    assert records[0].experiment_id == "experiment-a"

    # A second experiment (different id) with the same structural fingerprint
    # must be rejected BEFORE any holdout bar is read.
    with pytest.raises(HoldoutLockedError):
        run_portfolio_experiment(
            bars,
            spec("experiment-b"),
            factory,
            unlock_holdout=True,
            holdout_registry=registry,
        )


def test_stale_pending_expires_and_holdout_can_retry(tmp_path: Path) -> None:
    registry = HoldoutRegistry(tmp_path / "registry.json")
    fingerprint = _fingerprint()
    registry.begin_evaluation(fingerprint, "experiment-a")
    with pytest.raises(HoldoutLockedError):
        registry.preflight(fingerprint, "experiment-a")
    # A crashed evaluation (stale PENDING) is recovered automatically.
    expired = registry.expire_stale_pending(ttl_seconds=0)
    assert expired == 1
    registry.preflight(fingerprint, "experiment-a")  # no longer locked
    registry.begin_evaluation(fingerprint, "experiment-a")
    registry.complete_evaluation(fingerprint, "experiment-a", "h")
    # Fresh pending records are never expired. A different structural
    # fingerprint (different holdout window) is required here because the
    # completed one above is locked forever.
    fresh = HoldoutFingerprint(
        market=Market.EQUITY,
        timeframe="1d",
        universe_snapshot_hash="universe-hash",
        holdout_start=datetime(2027, 2, 1, tzinfo=UTC),
        holdout_end=datetime(2027, 8, 1, tzinfo=UTC),
        adjustment="all",
        source_provider="stooq",
    )
    registry.begin_evaluation(fresh, "experiment-c")
    assert registry.expire_stale_pending(ttl_seconds=3600) == 0
