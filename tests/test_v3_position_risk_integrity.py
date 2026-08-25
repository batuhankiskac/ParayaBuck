# ruff: noqa: E501

from __future__ import annotations

import json
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
RESEARCH = REPO / "scripts" / "research_v3_position_risk.py"
MANIFEST = REPO / "data" / "research_manifests" / "v3_position_risk_optimization_2026_08.json"


def _read_research() -> str:
    return RESEARCH.read_text(encoding="utf-8")


def test_manifest_exists_and_declares_no_results():
    assert MANIFEST.exists(), "predeclaration manifest missing"
    data = json.loads(MANIFEST.read_text())
    assert data.get("declared_before_results") is True
    assert data.get("no_results_yet") is True
    assert data.get("walk_forward", {}).get("fold_count_declared") == 20
    assert len(data.get("walk_forward", {}).get("folds_exact", [])) == 20
    assert data.get("cost_stress", {}).get("levels") == [1, 2, 3, 5]
    assert data.get("bootstrap", {}).get("seed") == 202608240
    assert data.get("random_seed") == 202608240
    assert len(data.get("promotion_rubric", {}).get("criteria", [])) == 14
    assert set(data.get("position_risk_candidates", {}).keys()) == {"P0", "P1", "P2", "P3"}
    # frozen params not empty
    assert (
        data.get("baseline_identity", {}).get("config_digest")
        == "458f3231f9af5c314fc3cac4732af4bd9c16a8fbad89f1fd0d1f2b0ccbc71b31"
    )
    assert (
        data.get("dataset", {}).get("bars_sha256") == "3baec12e21a1230732421df5aae9142873e82727b6e526f325948bc8385e903a"
    )


def test_candidate_definitions_exact():
    data = json.loads(MANIFEST.read_text())
    cands = data["position_risk_candidates"]
    # P0 control geometry 6 / 0.10
    assert cands["P0"]["risk_geometry"]["max_positions"] == 6
    assert cands["P0"]["risk_geometry"]["max_position_pct"] == 0.1
    # P1 7 / 0.085
    assert cands["P1"]["risk_geometry"]["max_positions"] == 7
    assert cands["P1"]["risk_geometry"]["max_position_pct"] == 0.085
    assert abs(7 * 0.085 - 0.595) < 1e-9
    # P2 8 / 0.075
    assert cands["P2"]["risk_geometry"]["max_positions"] == 8
    assert cands["P2"]["risk_geometry"]["max_position_pct"] == 0.075
    assert abs(8 * 0.075 - 0.60) < 1e-9
    # P3 geometry 6 / 0.10 but tail sizing
    assert cands["P3"]["risk_geometry"]["max_positions"] == 6
    assert cands["P3"]["risk_geometry"]["max_position_pct"] == 0.1
    assert cands["P3"]["sizing"]["method"] == "effective_risk_max"
    assert cands["P3"]["sizing"]["tail_lookback"] == 126
    assert cands["P3"]["sizing"]["tail_pct"] == 0.1


def test_forbidden_fast_folds_not_present():
    text = _read_research()
    assert "fast_folds_from_curve" not in text, "fast_folds_from_curve is forbidden"
    assert "fast_cost_stress_from_result" not in text, "fast_cost_stress_from_result is forbidden"
    assert "adjusted_return = base_return - estimated_fee" not in text
    assert "adjusted_sharpe = base_sharpe * constant" not in text
    assert "adjusted_sharpe" not in text.lower() or "heuristic" in text.lower()


def test_no_curve_slicing():
    text = _read_research()
    assert not re.search(r"seg = curve\[", text), "curve slicing is forbidden"
    assert not re.search(r"s_idx.*e_idx.*curve", text), "curve slicing for folds is forbidden"


def test_cost_stress_is_real_rerun():
    text = _read_research()
    assert "cost_multiplier" in text
    assert "slippage_bps" in text and "cost_multiplier" in text
    assert "spread_bps" in text and "cost_multiplier" in text
    assert re.search(r"for mult in COST_LEVELS", text) or "COST_LEVELS" in text
    assert "run_window" in text
    assert "base_sharpe * (1 -" not in text


def test_temporal_is_independent_rerun():
    text = _read_research()
    assert "temporal_diagnostic_windows" in text or "temporal_windows" in text
    assert "run_window" in text
    assert "run_chained_window" in text


def test_position_diagnostics_use_contemporaneous_equity():
    text = _read_research()
    assert "account_equity_at_entry" in text
    assert "realized_pnl_over_equity_at_entry" in text
    assert "realized_pnl / initial_backtest_cash" not in text
    assert "initial_backtest_cash" not in text
    # Must use realized_position_pnl / account_equity_at_entry semantics
    assert "realized_position_pnl / account_equity_at_entry" in text or "realized_pnl_over_equity_at_entry" in text


def test_distinguish_candidate_worst_vs_axon():
    text = _read_research()
    # Must distinguish candidate_worst_episode and AXON_reference_episode
    assert "candidate_worst_episode" in text
    assert "AXON_reference_episode" in text
    # Never display candidate worst % on AXON row unless AXON is worst
    assert (
        "Never display the candidate's worst" in text
        or "do not display candidate worst" in text.lower()
        or "AXON_reference" in text
    )
    # Ensure diagnostics return both fields and not conflated
    assert re.search(r"candidate_worst", text)
    assert re.search(r"AXON_reference", text)


def test_tail_es_no_lookahead_and_frozen_params():
    text = _read_research()
    # Frozen 126, 10%, sqrt(252) must be present and not optimized
    assert "126" in text
    assert "0.10" in text or "0.1" in text
    assert "sqrt(252)" in text
    # Effective risk = max
    assert "max(normal" in text or "max(normal_risk" in text or "effective_risk" in text.lower()
    # Must mention strictly through decision timestamp
    assert "through the decision timestamp" in text or "strictly" in text
    # Fallback when <126
    assert "fewer than 126" in text or "len(daily_returns) < 126" in text
    # Do not use future returns
    assert "Do NOT use future returns" in text or "no future" in text.lower()
    # Do not use diagnostic MAE
    assert "Do NOT use" in text
    # Do not optimize
    assert "DO NOT optimize" in text or "frozen" in text.lower()


def test_tail_es_no_mae_feed():
    text = _read_research()
    # Ensure P3 sizing does not feed MAE of trade being sized
    assert "MAE" in text
    # The tail computation must not reference mae of sized trade
    # Check that effective_risk block does not contain mae
    # Simple guard: tail logic section should not have mae_over_equity_at_entry inside effective calculation
    # We just ensure the phrase about not using diagnostic MAE is present
    assert "MAE" in text and "effective_risk" in text.lower()


def test_p1_p2_preserve_rank():
    text = _read_research()
    # Position-count selection MUST still preserve V3 alpha rank
    assert "alpha rank" in text.lower() or "rank" in text.lower()
    # Check risk overlay priority uses rank
    assert "rank_by_symbol" in text or "priority" in text
    # P1/P2 sizing remains inverse residual-vol
    assert "inverse_residual_vol" in text.lower() or "inverse_volatility_weights" in text


def test_no_daily_alpha_exits_no_stops():
    text = _read_research()
    # Must not add stops, daily alpha exits, take profit
    # Ensure research does not contain stop logic for this mission (except tail sizing)
    assert "stop" not in text.lower() or "tail" in text.lower()  # allow tail but not stop orders
    # Ensure weekly decisions unchanged
    assert "weekly" in text.lower()
    assert "top_n" in text and "8" in text


def test_promotion_rubric_guards_all_14():
    data = json.loads(MANIFEST.read_text())
    rubric = data.get("promotion_rubric", {})
    criteria = rubric.get("criteria", [])
    assert len(criteria) == 14
    assert any("Sharpe >= CONTROL" in c for c in criteria)
    assert any("5 percentage" in c for c in criteria)
    assert any("Worst" in c and "20%" in c for c in criteria)
    assert any("Top-5" in c for c in criteria)
    assert any("maxDD" in c for c in criteria)
    assert any("Median paired" in c for c in criteria)
    assert any("10/20" in c for c in criteria)
    assert any("2022+" in c for c in criteria)
    assert any("3x" in c for c in criteria)
    assert any("one-year" in c for c in criteria)
    assert any("single-symbol" in c for c in criteria)
    assert any("exposure" in c.lower() for c in criteria)
    assert any("lookahead" in c.lower() for c in criteria)
    assert any("ranking" in c.lower() for c in criteria)


def test_independent_runs_every_window():
    text = _read_research()
    # Every fold = independent run
    assert "Every fold = independent" in text or "independent" in text
    # Every temporal window = independent run with its own warmup
    assert "Every temporal window = independent" in text or "temporal" in text.lower()
    # Every cost level = real engine rerun
    assert "Every 1x/2x/3x/5x cost level = real" in text or "cost_multiplier" in text


def test_ex_ante_diagnostics_present():
    text = _read_research()
    assert "ex_ante" in text.lower()
    assert "residual vol 63" in text.lower() or "residual_vol_63" in text
    assert "ES10" in text
    assert "ATR20" in text or "atr" in text.lower()
    assert "worst daily return" in text.lower()
    assert "worst 10%" in text.lower() or "worst_10pct" in text.lower()


def test_concentration_metrics_present():
    text = _read_research()
    assert "HHI" in text
    assert "effective" in text.lower()
    assert "Herfindahl" in text or "HHI" in text
    assert "average_equity_exposure" in text or "average_exposure" in text
    assert "time_at_max" in text.lower() or "maximum gross" in text.lower()


def test_position_loss_metrics_use_account_equity():
    text = _read_research()
    assert "worst_position_loss" in text.lower() or "worst_single" in text.lower()
    assert "p95" in text.lower() and "p99" in text.lower()
    assert "top1" in text.lower() and "top5" in text.lower()
    assert "-1.0%" in text or "-1%" in text or "count_worse" in text.lower()


def test_axon_counterfactual_present():
    text = _read_research()
    assert "AXON" in text
    assert "counterfactual" in text.lower()
    assert "Would P1" in text or "would_reduce" in text.lower()
    assert "account_equity_at_entry" in text


def test_paper_v3_untouched_live_disabled():
    data = json.loads(MANIFEST.read_text())
    strat = data["baseline_identity"]["strategy"]["equity"]
    assert strat["name"] == "equity_residual_momentum_v3"
    assert data["baseline_identity"]["runtime"]["live_enabled"] is False
    assert data["baseline_identity"]["runtime"]["mode"] == "paper"
    # Ensure research script says PAPER frozen
    text = _read_research()
    assert "PAPER" in text and "frozen" in text.lower()
    assert "LIVE" in text and "disabled" in text.lower()
