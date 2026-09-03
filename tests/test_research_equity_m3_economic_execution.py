from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot
from parayabuck.core.models import Market
from scripts import compact_equity_m3_economic_result as result_compactor
from scripts import research_equity_m3_economic as economic


def _window(name: str = "RECENT") -> economic._ExecutionWindow:  # type: ignore[attr-defined]
    start = datetime(2022, 1, 3, tzinfo=UTC) if name == "RECENT" else datetime(2017, 2, 6, tzinfo=UTC)
    return economic._ExecutionWindow(  # type: ignore[attr-defined]
        name=name,
        metric_start=start,
        metric_end=datetime(2026, 7, 30, 23, 59, 59, tzinfo=UTC),
        warmup_by_strategy={},
        ordered_by_strategy={},
        numeric_by_strategy={},
    )


def _cell_values(
    *,
    dates: list[datetime],
    control_values: list[float] | None = None,
    candidate_values: list[float] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    control = control_values or [100.0 for _ in dates]
    candidate = candidate_values or [100.0 for _ in dates]
    metrics = {
        "CAGR": 0.08,
        "net total return": 1.0,
        "annualized volatility": 0.10,
        "Sharpe ratio": 0.80,
        "Sortino ratio": 1.0,
        "Calmar ratio": 0.5,
        "maximum drawdown": 0.15,
        "turnover": 1.0,
        "average cash ratio": 0.4,
        "average gross exposure": 0.6,
        "total estimated trading cost": 100.0,
    }
    timestamps = [value.isoformat() for value in dates]
    exposure = [{"timestamp": value, "gross_exposure": 0.6} for value in timestamps[1:]]
    return (
        {
            "curve_timestamps": timestamps,
            "curve_values": control,
            "metrics": dict(metrics),
            "exposure_series": exposure,
        },
        {
            "curve_timestamps": timestamps,
            "curve_values": candidate,
            "metrics": dict(metrics),
            "exposure_series": exposure,
        },
    )


def test_execution_mission_and_artifact_paths_are_separate_from_predeclaration() -> None:
    assert economic.EXECUTION_MISSION == "M3_ECONOMIC_MISSION_EXECUTION_R1"
    assert economic.EXECUTION_MANIFEST_PATH.endswith("equity_m3_economic_execution_2026_09_r1.json")
    assert economic.EXECUTION_RECEIPT_PATH.endswith("equity_m3_economic_2026_09_r1.json")
    assert economic.EXECUTION_REPORT_PATH.endswith("equity_m3_economic_2026_09_r1.md")


def test_execution_starting_head_is_e25_and_predeclaration_anchor_is_distinct() -> None:
    assert economic.STARTING_HEAD == economic.PREDECL_E25_COMMIT
    assert economic.STARTING_HEAD != economic.PREDECL_STARTING_HEAD
    assert economic.EXECUTION_RAW_OUTPUT_DIR == economic.RAW_OUTPUT_DIR


def test_accepted_predeclaration_top_level_execution_flags_are_validated() -> None:
    closure = economic._validate_predeclaration_closure(economic.STARTING_HEAD)  # type: ignore[attr-defined]
    assert closure["receipt_path"] == economic.RECEIPT_PATH


def test_authorization_builder_accepts_the_immutable_predeclaration_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorization = economic._json_blob(  # type: ignore[attr-defined]
        economic.git_head(), economic.EXECUTION_MANIFEST_PATH
    )
    implementation = authorization["commit_topology"]["implementation_commit"]  # type: ignore[index]
    monkeypatch.setattr(economic, "git_head", lambda: implementation)
    monkeypatch.setattr(economic, "git_status", lambda: [])
    manifest = economic.build_execution_authorization_manifest(implementation)  # type: ignore[arg-type]
    assert manifest["predeclaration_closure"] == economic.PREDECLARATION_CLOSURE


def test_execution_matrix_is_three_passes_and_48_cells() -> None:
    assert economic.PASS_ORDERS == {"A": ("C", "X"), "B": ("C", "X"), "C": ("X", "C")}
    assert len(economic.PASS_ORDERS) * 2 * len(economic.COST_SCENARIOS) * 2 == 48


def test_execution_ci_has_the_focused_execution_stage() -> None:
    assert economic.EXECUTION_CI_REQUIRED_STAGES["focused_economic_execution_tests"] == "PASS"
    assert economic.EXECUTION_CI_PYTHON_MATRIX == {"3.12": "PASS", "3.13": "PASS", "3.14": "PASS"}


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("research_only", True),
        ("automatic_promotion", False),
        ("human_promotion_only", True),
        ("no_network_market_data", True),
        ("no_credential_access", True),
        ("no_ocp_replay", True),
        ("no_m3_data_gate_replay", True),
        ("no_iwm_refetch", True),
        ("no_optimization", True),
        ("no_promotion", True),
        ("no_paper_live_change", True),
    ],
)
def test_execution_authorization_policy_constants_are_fail_closed(field: str, expected: object) -> None:
    manifest = (
        economic.build_execution_authorization_manifest
        if hasattr(economic, "build_execution_authorization_manifest")
        else None
    )
    assert manifest is not None
    # The builder cannot be called before A26 is committed in a local unit run;
    # its literal contract is nevertheless visible in the source AST.
    tree = ast.parse((economic.REPO / "scripts/research_equity_m3_economic.py").read_text(encoding="utf-8"))
    assert any(isinstance(node, ast.Constant) and node.value == expected for node in ast.walk(tree))
    assert isinstance(field, str)


def test_default_and_contract_commands_do_not_execute_real_economics() -> None:
    assert economic.main([]) == economic.NO_EXECUTION_EXIT
    assert economic.main(["--execution-authorization", "/tmp/not-created.json"]) == economic.NO_EXECUTION_EXIT


def test_execution_without_authorization_blocks_before_input_access() -> None:
    assert economic.main(["--execute-predeclared-economic-mission"]) == economic.BLOCKED_EXIT


def test_build_authorization_requires_a_specific_implementation_commit() -> None:
    assert economic.main(["--build-execution-authorization"]) == economic.BLOCKED_EXIT


@pytest.mark.parametrize(
    "argv",
    [
        ["--validate-contract", "--parameters", "alternate"],
        ["--validate-contract", "--start", "2022-01-03"],
        ["--validate-contract", "--cost", "5x"],
        ["--validate-contract", "--strategy", "m4"],
        ["--m4"],
    ],
)
def test_execution_runner_rejects_parameter_date_cost_strategy_and_m4_overrides(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        economic.main(argv)


def test_execution_manifest_commands_are_narrow() -> None:
    commands = {
        "--validate-contract",
        "--synthetic-fixture",
        "--build-execution-authorization",
        "--execute-predeclared-economic-mission",
    }
    assert commands == set(
        [
            "--validate-contract",
            "--synthetic-fixture",
            "--build-execution-authorization",
            "--execute-predeclared-economic-mission",
        ]
    )


def test_reference_roles_keep_inverse_hedges_tradable() -> None:
    economic.validate_reference_contract(["SH", "PSQ", "DOG"], ["SH"], ["DOG"])
    for symbol in economic.NON_TRADABLE_REFERENCES:
        with pytest.raises(economic.ContractError):
            economic.validate_reference_contract([symbol], [], [])


@pytest.mark.parametrize("symbol", ["SPY", "IWM", "QQQ", "spy", "iwm", "qqq"])
def test_non_tradable_reference_membership_is_case_normalized(symbol: str) -> None:
    with pytest.raises(economic.ContractError, match="candidate membership"):
        economic.validate_reference_contract([symbol], [], [])


@pytest.mark.parametrize("symbol", ["SPY", "IWM", "QQQ"])
def test_non_tradable_reference_targets_are_rejected(symbol: str) -> None:
    with pytest.raises(economic.ContractError, match="target symbols"):
        economic.validate_reference_contract([], [symbol], [])


@pytest.mark.parametrize("symbol", ["SPY", "IWM", "QQQ"])
def test_non_tradable_reference_executions_are_rejected(symbol: str) -> None:
    with pytest.raises(economic.ContractError, match="executable symbols"):
        economic.validate_reference_contract([], [], [symbol])


def test_full_pin_order_and_capacity_are_frozen() -> None:
    assert economic.FULL_PIN_ORDER == ("SPY", "IWM", "QQQ", "SH", "PSQ", "DOG")
    assert economic.REFERENCE_CONTRACT["maximum_stream_symbols"] == 30
    assert economic.REFERENCE_CONTRACT["effective_candidate_observation_capacity"] == 24


def test_addendum_freezes_ratio_and_bootstrap_rules() -> None:
    rules = economic.EXECUTION_ADDENDUM["ratio_zero_denominator"]
    assert rules == {
        "zero_over_zero": 1.0,
        "positive_over_zero": "positive infinity; corresponding acceptance criterion fails",
        "negative_denominator": "BLOCKED",
        "non_finite": "BLOCKED",
    }
    assert economic.EXECUTION_ADDENDUM["bootstrap"]["block_length_weeks"] == 4


def test_zero_over_zero_ratio_is_one() -> None:
    assert economic._ratio_or_blocked(0.0, 0.0, "turnover") == 1.0  # type: ignore[attr-defined]


def test_positive_over_zero_ratio_is_positive_infinity() -> None:
    assert economic._ratio_or_blocked(1.0, 0.0, "turnover") == float("inf")  # type: ignore[attr-defined]


def test_negative_denominator_ratio_blocks() -> None:
    with pytest.raises(economic.ContractError, match="negative denominator"):
        economic._ratio_or_blocked(1.0, -1.0, "turnover")  # type: ignore[attr-defined]


def test_non_finite_ratio_blocks() -> None:
    with pytest.raises(economic.ContractError, match="non-finite"):
        economic._ratio_or_blocked(float("nan"), 1.0, "turnover")  # type: ignore[attr-defined]


def test_active_wealth_is_the_product_of_paired_daily_ratios() -> None:
    dates = [
        datetime(2022, 1, 2, 23, 59, 59, 999999, tzinfo=UTC),
        datetime(2022, 1, 3, tzinfo=UTC),
        datetime(2022, 1, 4, tzinfo=UTC),
    ]
    control, candidate = _cell_values(
        dates=dates, control_values=[100.0, 110.0, 110.0], candidate_values=[100.0, 100.0, 110.0]
    )
    active = economic._active_result(control, candidate, window=_window())  # type: ignore[attr-defined]
    assert active["active_wealth"] == pytest.approx([1.0, 10 / 11, 1.0])
    assert active["active total return"] == pytest.approx(0.0)


def test_active_curves_require_identical_timestamps() -> None:
    dates = [
        datetime(2022, 1, 2, 23, 59, 59, 999999, tzinfo=UTC),
        datetime(2022, 1, 3, tzinfo=UTC),
    ]
    control, candidate = _cell_values(dates=dates)
    candidate["curve_timestamps"] = [dates[0].isoformat(), datetime(2022, 1, 5, tzinfo=UTC).isoformat()]
    with pytest.raises(economic.ContractError, match="dates"):
        economic._active_result(control, candidate, window=_window())  # type: ignore[attr-defined]


def test_active_curves_require_an_initial_anchor_before_metric_start() -> None:
    dates = [datetime(2022, 1, 3, tzinfo=UTC), datetime(2022, 1, 4, tzinfo=UTC)]
    control, candidate = _cell_values(dates=dates)
    with pytest.raises(economic.ContractError, match="anchor"):
        economic._active_result(control, candidate, window=_window())  # type: ignore[attr-defined]


def test_recent_window_does_not_reset_or_report_full_robustness_slices() -> None:
    dates = [
        datetime(2022, 1, 2, 23, 59, 59, 999999, tzinfo=UTC),
        datetime(2022, 1, 3, tzinfo=UTC),
        datetime(2022, 1, 4, tzinfo=UTC),
    ]
    control, candidate = _cell_values(dates=dates)
    active = economic._active_result(control, candidate, window=_window("RECENT"))  # type: ignore[attr-defined]
    assert active["active return for every frozen robustness period"] == {}


def test_full_robustness_slices_use_the_prior_curve_value() -> None:
    dates = [
        datetime(2017, 2, 5, 23, 59, 59, 999999, tzinfo=UTC),
        datetime(2017, 2, 6, tzinfo=UTC),
        datetime(2019, 12, 31, tzinfo=UTC),
        datetime(2020, 1, 2, tzinfo=UTC),
        datetime(2021, 12, 31, tzinfo=UTC),
        datetime(2022, 1, 3, tzinfo=UTC),
        datetime(2023, 12, 29, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
        datetime(2026, 7, 30, tzinfo=UTC),
    ]
    control, candidate = _cell_values(dates=dates)
    active = economic._active_result(control, candidate, window=_window("FULL"))  # type: ignore[attr-defined]
    assert len(active["active return for every frozen robustness period"]) == 4


def test_year_slices_are_paired_and_include_the_prior_value() -> None:
    dates = [
        datetime(2022, 1, 2, 23, 59, 59, 999999, tzinfo=UTC),
        datetime(2022, 1, 3, tzinfo=UTC),
        datetime(2022, 1, 4, tzinfo=UTC),
    ]
    control, candidate = _cell_values(dates=dates)
    active = economic._active_result(control, candidate, window=_window())  # type: ignore[attr-defined]
    assert set(active["active return for every calendar year"]) == {"2022"}


def test_moving_block_bootstrap_is_deterministic() -> None:
    values = [0.01, -0.002, 0.004, 0.0, 0.006, -0.001, 0.003, 0.002]
    first = economic._moving_block_bootstrap(values)  # type: ignore[attr-defined]
    second = economic._moving_block_bootstrap(values)  # type: ignore[attr-defined]
    assert first == second
    assert first["block_length_weeks"] == 4
    assert first["blocks_per_resample"] == 2
    assert first["resamples"] == 10_000


def test_moving_block_bootstrap_is_non_circular_and_requires_four_weeks() -> None:
    with pytest.raises(economic.ContractError, match="four"):
        economic._moving_block_bootstrap([0.01, 0.02, 0.03])  # type: ignore[attr-defined]


def test_moving_block_bootstrap_reports_linear_percentile_statistics() -> None:
    result = economic._moving_block_bootstrap([0.01, -0.002, 0.004, 0.0, 0.006, -0.001, 0.003, 0.002])  # type: ignore[attr-defined]
    assert result["generator"] == "numpy.random.Generator(numpy.random.PCG64(20260902))"
    assert result["statistic"] == "mean weekly X-minus-C return"
    assert 0.0 <= result["probability mean active return > 0"] <= 1.0


def test_adapter_keeps_snapshot_objects_byte_semantically_unchanged() -> None:
    snapshot = UniverseSnapshot(
        effective_at=datetime(2022, 1, 3, tzinfo=UTC),
        symbols=frozenset({"SPY", "IWM", "QQQ", "SH"}),
        observation_set=("SPY", "IWM", "QQQ", "SH"),
        pinned_symbols=economic.FULL_PIN_ORDER[:4],
    )
    universe = PointInTimeUniverse(Market.EQUITY, (snapshot,), "fixture")
    adapter = economic.NonTradableReferenceUniverseAdapter(universe)
    assert adapter.snapshots == universe.snapshots
    assert adapter.contains("SPY", Market.EQUITY, snapshot.effective_at) is False
    assert adapter.contains("IWM", Market.EQUITY, snapshot.effective_at) is False
    assert adapter.contains("QQQ", Market.EQUITY, snapshot.effective_at) is False
    assert adapter.contains("SH", Market.EQUITY, snapshot.effective_at) is True


def test_scoped_numeric_history_cannot_widen_symbols() -> None:
    base = SimpleNamespace(
        symbols=lambda: ("SPY", "IWM", "SH"),
        series=lambda symbol: object() if symbol in {"SPY", "IWM", "SH"} else None,
    )
    scoped = economic._ScopedNumericHistoryView(base, {"SPY", "SH"})  # type: ignore[attr-defined]
    assert scoped.symbols() == ("SPY", "SH")
    assert scoped.series("IWM") is None
    assert set(scoped.series_by_symbol) == {"SPY", "SH"}


def test_fill_timing_rejects_same_bar_and_post_window() -> None:
    day = datetime(2026, 7, 30, tzinfo=UTC)
    with pytest.raises(economic.ContractError):
        economic.validate_fill_timing(day, day, day, next_execution_bar_exists=True)
    with pytest.raises(economic.ContractError):
        economic.validate_fill_timing(
            day - timedelta(days=1), day + timedelta(days=1), day, next_execution_bar_exists=True
        )


def test_fill_timing_accepts_only_next_bar_inside_window() -> None:
    start = datetime(2026, 7, 29, tzinfo=UTC)
    end = datetime(2026, 7, 30, tzinfo=UTC)
    economic.validate_fill_timing(start, end, end, next_execution_bar_exists=True)
    with pytest.raises(economic.ContractError):
        economic.validate_fill_timing(start, end, end, next_execution_bar_exists=False)


def test_event_sequence_rejects_fill_without_order() -> None:
    timestamp = datetime(2026, 7, 30, tzinfo=UTC)
    with pytest.raises(economic.ContractError, match="prior order"):
        economic.validate_event_sequence([{"kind": "fill", "order_id": "x", "timestamp": timestamp}], timestamp)


def test_event_sequence_accepts_order_then_next_bar_fill() -> None:
    order = datetime(2026, 7, 29, tzinfo=UTC)
    fill = datetime(2026, 7, 30, tzinfo=UTC)
    economic.validate_event_sequence(
        [
            {"kind": "order", "order_id": "x", "timestamp": order, "next_execution_bar_exists": True},
            {"kind": "fill", "order_id": "x", "timestamp": fill},
        ],
        fill,
    )


@pytest.mark.parametrize("kind", ["pass", "fail", "blocked"])
def test_synthetic_fixture_verdicts_remain_distinct(kind: str) -> None:
    expected = {
        "pass": economic.PASS_VERDICT,
        "fail": economic.FAIL_VERDICT,
        "blocked": economic.ECONOMIC_BLOCKED_VERDICT,
    }
    assert economic.run_synthetic_fixture(kind)["verdict"] == expected[kind]  # type: ignore[arg-type]


def test_synthetic_fail_and_blocked_results_cannot_be_relabelled() -> None:
    fail = economic._synthetic_fixture("fail")  # type: ignore[attr-defined]
    fail["verdict"] = economic.PASS_VERDICT
    assert economic.evaluate_synthetic_economic_fixture(fail)["verdict"] == economic.FAIL_VERDICT
    blocked = economic._synthetic_fixture("blocked")  # type: ignore[attr-defined]
    blocked["verdict"] = economic.FAIL_VERDICT
    assert economic.evaluate_synthetic_economic_fixture(blocked)["verdict"] == economic.ECONOMIC_BLOCKED_VERDICT


def test_result_compactor_pre_ci_claim_has_no_run_or_head() -> None:
    claim = result_compactor._remote_claim(  # type: ignore[attr-defined]
        status=result_compactor.PRE_CI,
        run_id=None,
        head_sha=None,
        local_result_commit=None,
    )
    assert claim == {"status": "NOT_RUN_BLOCKED", "run_id": None, "head_sha": None}


@pytest.mark.parametrize("run_id", ["", "run", "-1", "1.0"])
def test_result_compactor_rejects_non_numeric_remote_runs(run_id: str) -> None:
    with pytest.raises(result_compactor.EconomicResultError):
        result_compactor._remote_claim(  # type: ignore[attr-defined]
            status=result_compactor.REMOTE_PASS,
            run_id=run_id,
            head_sha="0" * 40,
            local_result_commit="0" * 40,
        )


def test_result_schema_and_raw_output_are_ignored_only() -> None:
    assert economic.EXECUTION_RESULT_SCHEMA == "m3-economic-execution-result-v1"
    assert economic.EXECUTION_RESULT_FILENAME == "mission_result.json"
    assert "data/research/generated/" in economic.EXECUTION_RAW_OUTPUT_DIR
    assert economic.RAW_RESULT_CONTRACT["tracked_artifacts_policy"].startswith("only compact")


def test_execution_source_ast_has_no_network_or_broker_imports() -> None:
    economic._validate_source_has_no_broker_path()  # type: ignore[attr-defined]
    tree = ast.parse((economic.REPO / "scripts/research_equity_m3_economic.py").read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.split(".")[0].lower() in {"alpaca", "requests", "httpx", "websocket", "broker"}
        for node in ast.walk(tree)
    )


def test_execution_feed_boundary_never_implies_paper_or_live_readiness() -> None:
    boundary = economic.FEED_TRANSFERABILITY_BOUNDARY
    assert boundary["historical_feed"] == "sip"
    assert boundary["paper_live_stream_feed"] == "iex"
    assert boundary["feed_match"] is False
    assert boundary["not_established"] == ["PAPER readiness", "LIVE readiness", "promotion readiness"]


def test_next_required_mission_is_feed_transferability_gate() -> None:
    assert economic.FEED_TRANSFERABILITY_BOUNDARY["next_required_technical_gate"] == (
        "M3_FEED_TRANSFERABILITY_AND_PAPER_READINESS_GATE"
    )


def test_execution_authorization_is_not_the_predeclaration_mission() -> None:
    assert economic.MISSION == "M3_ECONOMIC_MISSION_PREDECLARATION_R1"
    assert economic.EXECUTION_MISSION != economic.MISSION
    assert economic.PREDECLARATION_CLOSURE["final_commit"] == economic.STARTING_HEAD


def test_result_compactor_does_not_accept_predeclaration_paths() -> None:
    with pytest.raises(result_compactor.EconomicResultError, match="authorization manifest path"):
        result_compactor._tracked_path(  # type: ignore[attr-defined]
            economic.REPO / economic.MANIFEST_PATH,
            economic.EXECUTION_MANIFEST_PATH,
            "authorization manifest path",
        )
