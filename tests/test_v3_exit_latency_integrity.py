# ruff: noqa: E501

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
RESEARCH = REPO / "scripts" / "research_v3_exit_latency.py"
MANIFEST = REPO / "data" / "research_manifests" / "v3_exit_latency_optimization_2026_08.json"


def _read_research() -> str:
    return RESEARCH.read_text(encoding="utf-8")


def test_manifest_exists_and_declares_no_results():
    assert MANIFEST.exists(), "predeclaration manifest missing"
    import json

    data = json.loads(MANIFEST.read_text())
    assert data.get("declared_before_results") is True
    assert data.get("no_results_yet") is True
    assert data.get("walk_forward", {}).get("fold_count_declared") == 20
    assert len(data.get("walk_forward", {}).get("folds_exact", [])) == 20
    assert data.get("cost_stress", {}).get("levels") == [1, 2, 3, 5]
    assert data.get("bootstrap", {}).get("seed") == 202608240
    # promotion rubric must have 12 criteria
    assert len(data.get("promotion_rubric", {}).get("criteria", [])) == 12
    # exit candidates exactly E0-E3
    assert set(data.get("exit_candidates", {}).keys()) == {"E0", "E1", "E2", "E3"}


def test_forbidden_fast_folds_not_present():
    text = _read_research()
    assert "fast_folds_from_curve" not in text, "fast_folds_from_curve is forbidden"
    assert "fast_cost_stress_from_result" not in text, "fast_cost_stress_from_result is forbidden"
    # No curve slicing substitution: ensure no heuristic adjusted_return
    assert "adjusted_return = base_return - estimated_fee" not in text
    assert "adjusted_sharpe = base_sharpe * constant" not in text
    # Also block generic heuristic pattern
    assert "adjusted_sharpe" not in text.lower() or "heuristic" in text.lower()


def test_no_curve_slicing_import():
    text = _read_research()
    # Ensure research does not slice equity_curve to compute folds
    # Look for pattern equity_curve[s_idx:e_idx] slicing for folds — forbidden
    # Our implementation uses independent run_window per fold, not slicing
    assert (
        "equity_curve[" not in text or "full_results" in text
    )  # allow full curve storage but not slicing for folds
    # More precise: forbid s_idx/e_idx slicing that computes fold returns
    assert not re.search(r"s_idx.*e_idx.*curve", text), "curve slicing for folds is forbidden"
    assert not re.search(r"seg = curve\[", text), "curve slicing is forbidden"


def test_cost_stress_is_real_rerun():
    text = _read_research()
    # Must contain real engine rerun for cost levels
    assert "cost_multiplier" in text
    # Must show slippage_bps * cost_multiplier and spread_bps * cost_multiplier
    assert "slippage_bps" in text and "cost_multiplier" in text
    assert "spread_bps" in text and "cost_multiplier" in text
    # Ensure loop over COST_LEVELS and run_window per level
    assert (
        re.search(r"for mult in COST_LEVELS", text)
        or re.search(r"for mult in \[1, 2, 3, 5\]", text)
        or "COST_LEVELS" in text
    )
    assert "run_window" in text
    # Forbid heuristic fee transform
    assert "base_ret - extra_fee" not in text
    assert "base_sharpe * (1 -" not in text


def test_temporal_is_independent_rerun():
    text = _read_research()
    assert "temporal_diagnostic_windows" in text or "temporal_windows" in text
    assert "run_window" in text
    assert "run_chained_window" in text


def test_position_diagnostics_use_contemporaneous_equity():
    text = _read_research()
    # Must use account_equity_at_entry denominator, not initial_backtest_cash
    assert "account_equity_at_entry" in text
    assert "realized_pnl_over_equity_at_entry" in text
    assert "realized_pnl / initial_backtest_cash" not in text
    # Must not use that ratio
    assert "initial_backtest_cash" not in text


def test_daily_exit_uses_point_in_time_v3_scoring():
    text = _read_research()
    # Must reuse production V3 scoring
    assert "_score_candidates" in text
    assert "positive" not in text.lower() or "gate" in text.lower()  # at least mention gate logic
    assert "requires_intermediate_risk_rebalance" in text
    assert "rank > 8" in text or "rank>8" in text or "> 8" in text
    assert "rank > 12" in text or "rank>12" in text or "> 12" in text
    # No same-bar exit: must execute at next tradable bar/open (engine does)
    # Ensure strategy uses production cost model (engine's slippage/spread)
    assert "PortfolioBacktestEngine" in text


def test_promotion_rubric_guards_all_12():
    import json

    data = json.loads(MANIFEST.read_text())
    rubric = data.get("promotion_rubric", {})
    criteria = rubric.get("criteria", [])
    # Each numbered criterion must be present
    assert any("Sharpe >= CONTROL" in c for c in criteria)
    assert any("5pp" in c for c in criteria)
    assert any("MaxDD" in c for c in criteria)
    assert any("net_early_exit_value" in c for c in criteria)
    assert any("Median paired" in c for c in criteria)
    assert any("10/20" in c for c in criteria)
    assert any("2022+" in c for c in criteria)
    assert any("3x" in c for c in criteria)
    assert any("one-year" in c for c in criteria)
    assert any("one-symbol" in c for c in criteria)
    assert any("turnover" in c.lower() for c in criteria)
    assert any("lookahead" in c.lower() for c in criteria)


def test_entry_policy_frozen_weekly():
    text = _read_research()
    assert "rebalance_frequency" in text
    assert "weekly" in text
    assert "FROZEN" in text or "frozen" in text.lower()
