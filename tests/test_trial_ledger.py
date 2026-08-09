from __future__ import annotations

from pathlib import Path

from parayabuck.backtest.portfolio_experiments import PortfolioTrialResult
from parayabuck.backtest.trial_ledger import TrialLedger
from parayabuck.core.models import Market


def _trial(trial_id: str, experiment_id: str = "e1") -> PortfolioTrialResult:
    return PortfolioTrialResult(
        trial_id=trial_id,
        strategy_name="equity_etf_dual_momentum_v1",
        strategy_version="1.0.0",
        parameters=(("momentum_scheme", "12_1"),),
        fold_results=(),
        market=Market.EQUITY,
        total_rebalance_months=0,
        closed_positions=0,
        profit_factor=0.0,
        calmar=0.0,
        expectancy=0.0,
        deflated_sharpe_probability=0.0,
        probability_backtest_overfitting=0.0,
        max_drawdown=0.0,
        positive_fold_ratio=0.0,
        positive_month_ratio=0.0,
        stress_profit_factor=0.0,
        stress_expectancy=0.0,
        stress_realized_pnls=(),
        gross_profit_by_symbol=(),
        maximum_concentration=0.0,
        regime_diverse=False,
    )


def test_ledger_counts_distinct_hypotheses_per_research_program(tmp_path: Path) -> None:
    ledger = TrialLedger(tmp_path / "ledger.json")
    trials_a = (_trial("t1"), _trial("t2"))
    count = ledger.record_trials(
        dataset_id="equity_etf_1d_v2",
        dataset_hash="hash-v2",
        experiment_id="exp-1",
        trials=trials_a,
        research_program_id="equity-program",
    )
    assert count == 2
    # A second experiment on the SAME dataset adds to the cumulative count.
    count = ledger.record_trials(
        dataset_id="equity_etf_1d_v2",
        dataset_hash="hash-v2",
        experiment_id="exp-2",
        trials=(_trial("t3"), _trial("t4")),
        research_program_id="equity-program",
    )
    assert count == 4
    # Re-running the same experiment must NOT double-count.
    count = ledger.record_trials(
        dataset_id="equity_etf_1d_v2",
        dataset_hash="hash-v2",
        experiment_id="exp-1",
        trials=trials_a,
        research_program_id="equity-program",
    )
    assert count == 4
    # Extending/re-hashing the dataset cannot reset or duplicate a hypothesis.
    count = ledger.record_trials(
        dataset_id="equity_etf_1d_v3",
        dataset_hash="hash-v3",
        experiment_id="exp-3",
        trials=(_trial("t1"), _trial("t5")),
        research_program_id="equity-program",
        dataset_lineage=("equity_etf_1d_v2",),
    )
    assert count == 5
    assert ledger.trial_count_for_program("equity-program") == 5
    assert ledger.trial_count("hash-v2") == 4


def test_ledger_persists_and_reloads(tmp_path: Path) -> None:
    ledger = TrialLedger(tmp_path / "ledger.json")
    ledger.record_trials(
        dataset_id="equity_etf_1d_v2",
        dataset_hash="hash-v2",
        experiment_id="exp-1",
        trials=(_trial("t1"),),
    )
    reloaded = TrialLedger(tmp_path / "ledger.json")
    assert reloaded.trial_count("hash-v2") == 1
    assert reloaded.entries_for("hash-v2")[0]["trial_id"] == "t1"


def test_ledger_corrupt_file_fails_loudly(tmp_path: Path) -> None:
    import pytest

    path = tmp_path / "ledger.json"
    path.write_text("{not-json", encoding="utf-8")
    # A corrupt ledger must never silently reset the DSR trial count.
    with pytest.raises(ValueError):
        TrialLedger(path)
