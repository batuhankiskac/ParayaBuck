from __future__ import annotations

import copy
import csv
import inspect
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from parayabuck.backtest.portfolio_engine import PortfolioContext
from parayabuck.core.models import Bar, Market
from scripts import research_equity_m3_data_gate_r5 as r5
from scripts.research_equity_m3_m4 import ProxyMultifactorResidualMomentum


def _timestamp_series(count: int, *, start: datetime = datetime(2024, 1, 2, tzinfo=UTC)) -> list[datetime]:
    return [start + timedelta(days=index) for index in range(count)]


def _source_snapshot(effective: str, latest: str, symbols: list[str]) -> dict[str, object]:
    return {
        "effective_at": effective,
        "latest_input_timestamp": latest,
        "scanner_version": r5.R5_SCANNER_VERSION,
        "config_digest": r5.R5_SCANNER_CONFIG_DIGEST,
        "symbols": list(symbols),
        "eligible_symbols": list(symbols),
        "eligible_count": len(symbols),
        "candidate_count": len(symbols),
        "reference_symbols_excluded": [],
        "scanner_result_empty": not symbols,
        "strictly_prior_data": True,
        "prior_bar_count": 1,
    }


def _projection_contract(expected: list[str], cutoff: str) -> dict[str, object]:
    return {
        "evaluation_snapshot_contract": {
            "evaluation_decision_session": "2026-07-30",
            "evaluation_decision_cutoff_timestamp": cutoff,
            "terminal_observation_boundary_required": False,
            "expected_effective_at_timestamps": expected,
            "expected_effective_at_digest_sha256": r5.effective_timestamp_digest(expected),
        },
        "critical_git_blobs": {
            "scripts/research_equity_m3_data_gate_r5.py": {
                "git_blob_id": "a" * 40,
                "raw_sha256": "b" * 64,
            }
        },
    }


def _project_fixture() -> tuple[dict[str, object], dict[str, object]]:
    first = "2026-07-30T04:00:00.000000Z"
    post = "2026-07-31T04:00:00.000000Z"
    snapshots = [
        _source_snapshot(first, "2026-07-29T04:00:00.000000Z", ["AAA"]),
        _source_snapshot(post, first, ["AAA", "BBB"]),
    ]
    population = r5.deterministic_candidate_population(snapshots, reference_symbols=r5.REFERENCE_SYMBOLS)
    source = {
        "schema_version": "m3-pit-scanner-population-v1",
        "mission": "equity_m3_pit_scanner_population_2026_09_r4",
        "snapshots": snapshots,
        "population": {
            key: population[key]
            for key in (
                "candidate_universe_digest_sha256",
                "candidate_decision_eligibility_digest_sha256",
                "snapshot_count",
                "unique_candidate_count",
            )
        },
        "strict_prior_proof": {"status": "PASS"},
    }
    expected = [first]
    contract = _projection_contract(expected, first)
    identities = {
        "source": {"path": "data/research/generated/m3_data_gate_r4/pit_universe.json", "raw_sha256": "c" * 64},
        "r4_manifest": {"path": r5.R4_PIT_MANIFEST_PATH, "git_blob_id": "d" * 40, "raw_sha256": "e" * 64},
        "canonical": {"raw_sha256": "f" * 64},
        "sidecar": {"raw_sha256": "1" * 64},
        "composite": {"semantic_digest_sha256": "2" * 64},
        "contract": {"path": r5.R5_MANIFEST_PATH, "git_blob_id": "3" * 40, "raw_sha256": "4" * 64},
    }
    return source, {**contract, "_identities": identities}


def _run_projection(source: dict[str, object], contract: dict[str, object]) -> dict[str, object]:
    identities = contract.pop("_identities")
    return r5.project_evaluation_pit(
        source,
        contract=contract,
        source_identity=identities["source"],
        r4_manifest_identity=identities["r4_manifest"],
        canonical_identity=identities["canonical"],
        sidecar_identity=identities["sidecar"],
        composite_identity=identities["composite"],
        contract_identity=identities["contract"],
    )


def _matrix_panel(mode: str = "pass", count: int = 274) -> tuple[list[date], dict[str, list[float]]]:
    rng = np.random.default_rng(42)
    spy = rng.normal(0.0005, 0.01, count - 1)
    size = rng.normal(0.0002, 0.008, count - 1)
    growth = rng.normal(0.0003, 0.009, count - 1)
    if mode == "rank_deficient":
        size = spy.copy()
    elif mode == "ill_conditioned":
        size = spy + rng.normal(0.0, 1e-10, count - 1)
    stock = 0.5 * spy + 0.3 * size - 0.2 * growth + rng.normal(0.0, 0.002, count - 1)

    def closes(returns: np.ndarray) -> list[float]:
        return [100.0, *list(100.0 * np.cumprod(1.0 + returns))]

    sessions = [date(2024, 1, 2) + timedelta(days=index) for index in range(count)]
    spy_close = closes(spy)
    iwm_close = closes(spy + size)
    qqq_close = closes(spy + growth)
    return sessions, {"AAA": closes(stock), "SPY": spy_close, "IWM": iwm_close, "QQQ": qqq_close}


def test_d6_c6_lineage_and_prior_artifacts_are_unchanged() -> None:
    parent = subprocess.run(
        ["git", "show", "-s", "--format=%P", r5.R5_BASE_COMMIT], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert parent == "4f1ddd970207406f4a70d278651796bccd5b8f7a"
    changed = subprocess.run(
        ["git", "diff", "--name-only", f"{r5.R5_BASE_COMMIT}..HEAD"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert all(
        not path.startswith("data/research_manifests/equity_m3_data_gate_2026_09_r4")
        and not path.startswith("data/research_receipts/equity_m3_data_gate_2026_09_r4")
        and path != "docs/equity_m3_data_gate_2026_09_r4.md"
        for path in changed
    )


def test_r4_accepted_bundle_identity_is_frozen() -> None:
    assert r5.R4_RAW_BUNDLE_SHA256 == "9590e35d2f5b80a4cd7f5c2c755acfcc088a6f7696d6eb86039d702ee6832258"
    assert r5.R4_SOURCE_RECEIPT_SHA256 == "2dbeeebc688dab111013a114e9c08b44c116e23c48a42968099f6c8671ea4003"
    assert r5.R4_SIDECAR_SEMANTIC_SHA256 == "cc1855c607ee394fae9e5ede594d1420f0c4d03cb8a5af08e14e6a656cd7d837"
    assert r5.R4_COMPOSITE_SEMANTIC_SHA256 == "5bb66b7ff15d1f9ef3e9ce7caa4ef2314fcc670cf80b9696ee576e1b9cac4eda"


def test_r5_source_has_no_network_or_credential_read_path() -> None:
    source = inspect.getsource(r5)
    for forbidden in ("requests.", "urllib", "yfinance", "httpx", "socket.", "os.environ", "dotenv"):
        assert forbidden not in source


def test_full_timestamp_selection_does_not_use_date_only() -> None:
    population = {
        "snapshots": [
            _source_snapshot("2026-07-30T04:00:00.000000Z", "2026-07-29T04:00:00.000000Z", ["AAA"]),
            _source_snapshot("2026-07-30T10:00:00.000000Z", "2026-07-30T04:00:00.000000Z", ["BBB"]),
        ]
    }
    selected = r5.select_snapshot_for_cutoff(population, "2026-07-30T05:00:00.000000Z")
    assert selected["eligible_symbols"] == ["AAA"]
    assert r5.candidate_symbols_for_timestamp(population, "2026-07-30T05:00:00.000000Z") == ("AAA",)


def test_projection_excludes_post_evaluation_source_without_mutating_source() -> None:
    source, contract = _project_fixture()
    before = copy.deepcopy(source)
    projection = _run_projection(source, contract)
    assert source == before
    assert projection["r5_projection"]["source_snapshot_count"] == 2
    assert projection["r5_projection"]["evaluation_snapshot_count"] == 1
    assert projection["r5_projection"]["excluded_post_evaluation_snapshot_count"] == 1
    assert projection["r5_projection"]["evaluation_last_snapshot"] == "2026-07-30T04:00:00.000000Z"
    assert projection["snapshots"][0]["eligible_symbols"] == ["AAA"]


def test_projection_digest_is_deterministic() -> None:
    source_a, contract_a = _project_fixture()
    source_b, contract_b = _project_fixture()
    first = _run_projection(source_a, contract_a)
    second = _run_projection(source_b, contract_b)
    assert first["r5_projection"]["projection_digest_sha256"] == second["r5_projection"]["projection_digest_sha256"]
    assert first["population"] == second["population"]


def test_projection_missing_required_snapshot_blocks() -> None:
    source, contract = _project_fixture()
    contract["evaluation_snapshot_contract"]["expected_effective_at_timestamps"] = ["2026-07-29T04:00:00.000000Z"]
    contract["evaluation_snapshot_contract"]["expected_effective_at_digest_sha256"] = r5.effective_timestamp_digest(
        contract["evaluation_snapshot_contract"]["expected_effective_at_timestamps"]
    )
    with pytest.raises(r5.GateError, match="required evaluation snapshot is missing"):
        _run_projection(source, contract)


def test_projection_extra_consumed_snapshot_blocks() -> None:
    source, contract = _project_fixture()
    source["snapshots"].append(_source_snapshot("2026-07-30T05:00:00.000000Z", "2026-07-30T04:00:00.000000Z", ["AAA"]))
    source["snapshots"] = sorted(source["snapshots"], key=lambda item: item["effective_at"])
    population = r5.deterministic_candidate_population(source["snapshots"], reference_symbols=r5.REFERENCE_SYMBOLS)
    source["population"] = {
        key: population[key]
        for key in (
            "candidate_universe_digest_sha256",
            "candidate_decision_eligibility_digest_sha256",
            "snapshot_count",
            "unique_candidate_count",
        )
    }
    contract["evaluation_snapshot_contract"]["evaluation_decision_cutoff_timestamp"] = "2026-07-31T06:00:00.000000Z"
    with pytest.raises(r5.GateError, match="unexpected consumed snapshot"):
        _run_projection(source, contract)


def test_timing_contract_is_deterministic_and_terminal_is_not_post_eval() -> None:
    sessions = r5.expected_exchange_sessions(date(2016, 1, 4), date(2026, 7, 30))
    timestamps = r5.expected_evaluation_timestamps(sessions)
    assert len(sessions) == 2658
    assert timestamps[0] == "2016-01-04T05:00:00.000000Z"
    assert timestamps[-1] == "2026-07-30T04:00:00.000000Z"
    assert r5.effective_timestamp_digest(timestamps) == r5.effective_timestamp_digest(tuple(timestamps))
    assert r5.timestamp_for_session(date(2026, 7, 30)) == datetime(2026, 7, 30, 4, tzinfo=UTC)


def test_close_matrix_ignores_valid_post_evaluation_rows(tmp_path: Path) -> None:
    path = tmp_path / "canonical.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market"])
        writer.writerow(["2026-07-29T04:00:00Z", "AAA", "1d", "1", "2", "0.5", "1.5", "100", "equity"])
        writer.writerow(["2026-07-30T04:00:00Z", "AAA", "1d", "2", "3", "1.5", "2.5", "100", "equity"])
        writer.writerow(["2026-07-31T04:00:00Z", "AAA", "1Day", "3", "4", "2.5", "3.5", "100", "EQUITY"])

    close_arrays, timestamp_arrays, stats = r5._load_close_matrix(
        path,
        symbols=("AAA",),
        sessions=[date(2026, 7, 29), date(2026, 7, 30)],
        sidecar_rows=(),
    )

    assert stats["invalid_selected_row_count"] == 0
    assert stats["duplicate_selected_row_count"] == 0
    assert stats["selected_canonical_out_of_range_row_count"] == 1
    assert close_arrays["AAA"].tolist() == [1.5, 2.5]
    assert timestamp_arrays["AAA"].tolist() == [1785297600000000, 1785384000000000]


def test_symbolic_head_is_rejected_for_immutable_provenance() -> None:
    sessions = r5.expected_exchange_sessions(date(2016, 1, 4), date(2026, 7, 30))
    timestamps = list(r5.expected_evaluation_timestamps(sessions))
    contract: dict[str, object] = {
        "schema_version": "5.0.0",
        "mission": r5.R5_MISSION,
        "repository_url": "https://github.com/batuhankiskac/ParayaBuck",
        "branch": "main",
        "snapshot_date": "2026-09-02",
        "declared_before_results": True,
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "economic_mission_ready": False,
        "base_commit": r5.R5_BASE_COMMIT,
        "implementation_parent_commit": r5.R5_BASE_COMMIT,
        "implementation_commit": "a" * 40,
        "implementation_tree": "b" * 40,
        "manifest_path": r5.R5_MANIFEST_PATH,
        "manifest_commit_rule": "manifest-only: exactly the declared R5 manifest path",
        "critical_git_blobs": {
            path: {"git_blob_id": "c" * 40, "raw_sha256": "d" * 64} for path in r5.R5_CRITICAL_GIT_PATHS
        },
        "frozen_parameters": r5.R5_FROZEN_PARAMETERS,
        "numerical_thresholds": r5.R5_NUMERICAL_THRESHOLDS,
        "mandatory_data_pass_gates": list(r5.R5_MANDATORY_GATES),
        "evaluation_snapshot_contract": {
            "evaluation_decision_session": "2026-07-30",
            "evaluation_decision_cutoff_timestamp": timestamps[-1],
            "terminal_observation_boundary_required": False,
            "expected_effective_at_timestamps": timestamps,
            "expected_effective_at_digest_sha256": r5.effective_timestamp_digest(timestamps),
        },
        "no_network_policy": {
            "network_allowed": False,
            "credential_reads_allowed": False,
            "accepted_source": "exact accepted R4 captured bytes only",
            "refetch_allowed": False,
        },
        "economic_boundary": {
            "candidate_economics_run": False,
            "economic_metrics_present": False,
            "alpha_ranking_allowed": False,
            "optimization_allowed": False,
            "candidate_ocp_status": "NOT_RUN_OUT_OF_SCOPE",
            "next_required_gate": "M3_CANDIDATE_OCP_GATE",
        },
        "forbidden_economic_fields": r5.R5_FORBIDDEN_ECONOMIC_FIELDS,
        "immutable_provenance": {"implementation_commit": "a" * 40},
    }
    validate = copy.deepcopy(contract)
    validate["implementation_commit"] = "HEAD"
    with pytest.raises(r5.GateError, match="exact SHA"):
        r5.validate_r5_contract(validate)
    r5.validate_r5_contract(contract)


def test_frozen_minimum_history_comes_from_fresh_m3_strategy() -> None:
    assert (
        r5.frozen_m3_minimum_history_bars(r5.R5_FROZEN_PARAMETERS)
        == ProxyMultifactorResidualMomentum(r5.R5_FROZEN_PARAMETERS).minimum_history_bars
        == 274
    )


def test_scanner_eligible_100_bar_candidate_is_not_warm_and_live_scorer_skips() -> None:
    timestamps = _timestamp_series(100)
    references = {symbol: timestamps for symbol in r5.REFERENCE_SYMBOLS}
    result = r5.classify_candidate_history(
        candidate_symbol="AAA",
        scanner_eligible=True,
        stock_timestamps=timestamps,
        reference_timestamps=references,
        decision_cutoff=timestamps[-1],
        minimum_history_bars=274,
    )
    assert result["state"] == "SCANNER_ELIGIBLE_M3_NOT_WARM"
    bars = {
        symbol: tuple(
            Bar(
                timestamp=timestamp,
                symbol=symbol,
                timeframe="1d",
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1000.0,
                market=Market.EQUITY,
            )
            for index, timestamp in enumerate(timestamps)
        )
        for symbol in ("AAA", *r5.REFERENCE_SYMBOLS)
    }

    class Universe:
        def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
            return symbol == "AAA" and market == Market.EQUITY and timestamp == timestamps[-1]

    context = PortfolioContext(
        decision_timestamp=timestamps[-1],
        market=Market.EQUITY,
        timeframe="1d",
        history=bars,
        universe=Universe(),
        universe_version="synthetic",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )
    strategy = ProxyMultifactorResidualMomentum(r5.R5_FROZEN_PARAMETERS)
    scored, _vols = strategy._score_candidates(context)
    assert scored == ()
    assert result["state"] != "M3_MATRIX_NUMERICAL_FAIL"


def test_273_common_closes_remain_not_warm_and_274_enters_matrix_audit() -> None:
    timestamps = _timestamp_series(274)
    result_273 = r5.classify_candidate_history(
        candidate_symbol="AAA",
        scanner_eligible=True,
        stock_timestamps=timestamps[:-1],
        reference_timestamps={symbol: timestamps for symbol in r5.REFERENCE_SYMBOLS},
        decision_cutoff=timestamps[-1],
        minimum_history_bars=274,
    )
    result_274 = r5.classify_candidate_history(
        candidate_symbol="AAA",
        scanner_eligible=True,
        stock_timestamps=timestamps,
        reference_timestamps={symbol: timestamps for symbol in r5.REFERENCE_SYMBOLS},
        decision_cutoff=timestamps[-1],
        minimum_history_bars=274,
    )
    assert result_273["state"] == "SCANNER_ELIGIBLE_M3_NOT_WARM"
    assert result_274["state"] == "M3_MATRIX_ELIGIBLE"
    assert len(result_274["selected_timestamps"]) == 274


@pytest.mark.parametrize("mode", ["rank_deficient", "ill_conditioned"])
def test_eligible_bad_matrix_fails(mode: str) -> None:
    sessions, closes = _matrix_panel(mode)
    result = r5.audit_design_matrix("AAA", sessions[-1], sessions, closes, thresholds=r5.R5_NUMERICAL_THRESHOLDS)
    assert result["passed"] is False
    assert result["failure_categories"]


def test_eligible_finite_full_rank_matrix_passes() -> None:
    sessions, closes = _matrix_panel("pass")
    result = r5.audit_design_matrix("AAA", sessions[-1], sessions, closes, thresholds=r5.R5_NUMERICAL_THRESHOLDS)
    assert result["passed"] is True
    assert result["matrix_shape"] == [273, 3]
    assert result["return_count"] == 273
    assert result["matrix_rank"] == 3


def test_depth_warmup_and_post_warmup_rules() -> None:
    rows = [
        {"decision_session": "2016-01-04", "m3_matrix_eligible_count": 0, "numerical_fail_count": 0},
        {"decision_session": "2017-01-03", "m3_matrix_eligible_count": 8, "numerical_fail_count": 0},
        {"decision_session": "2017-01-10", "m3_matrix_eligible_count": 8, "numerical_fail_count": 0},
    ]
    depth = r5.candidate_depth_feasibility(
        rows,
        recent_decision_sessions=[date(2017, 1, 10)],
    )
    assert depth["full_initial_candidate_warmup_decision_count"] == 1
    assert depth["full_candidate_depth_pass"] is True
    assert depth["recent_candidate_depth_pass"] is True
    rows[-1] = {**rows[-1], "m3_matrix_eligible_count": 7}
    depth = r5.candidate_depth_feasibility(rows, recent_decision_sessions=[date(2017, 1, 10)])
    assert depth["full_candidate_depth_pass"] is False
    assert depth["recent_candidate_depth_pass"] is False


def test_not_warm_candidate_does_not_increment_numerical_failures() -> None:
    sessions = _timestamp_series(300)
    timestamps = {symbol: np.zeros(300, dtype=np.int64) for symbol in ("AAA", *r5.REFERENCE_SYMBOLS)}
    for symbol in timestamps:
        timestamps[symbol][:100] = [int(value.timestamp() * 1_000_000) for value in sessions[:100]]
    projection = {
        "snapshots": [
            _source_snapshot(
                r5.canonical_timestamp(sessions[-1]),
                r5.canonical_timestamp(sessions[-2]),
                ["AAA"],
            )
        ]
    }
    closes = {symbol: np.ones(300, dtype=np.float64) for symbol in timestamps}
    decision_session = sessions[99].date()
    projection["snapshots"][0]["effective_at"] = r5.canonical_timestamp(sessions[0])
    projection["snapshots"][0]["latest_input_timestamp"] = r5.canonical_timestamp(sessions[0] - timedelta(days=1))
    result = r5.audit_matrix_population(
        projection=projection,
        full_decisions=[decision_session],
        recent_decisions=[decision_session],
        close_arrays=closes,
        timestamp_arrays=timestamps,
        sessions=[value.date() for value in sessions],
    )
    assert result["m3_not_warm_pair_count"] == 1
    assert result["total_eligible_matrix_count"] == 0
    assert result["numerical_fail_count"] == 0


def test_unknown_or_not_run_mandatory_gate_blocks() -> None:
    gates = {name: {"status": r5.R5_GATE_PASS} for name in r5.R5_MANDATORY_GATES}
    gates["design_matrix_feasibility"] = {"status": r5.R5_GATE_UNKNOWN}
    assert r5.evaluate_mandatory_gates(gates) == "M3_DATA_GATE_BLOCKED"
    gates["design_matrix_feasibility"] = {"status": r5.R5_GATE_NOT_RUN}
    assert r5.evaluate_mandatory_gates(gates) == "M3_DATA_GATE_BLOCKED"


def test_projection_has_no_economic_score_or_metric_keys() -> None:
    source, contract = _project_fixture()
    projection = _run_projection(source, contract)
    assert not r5.forbidden_receipt_keys(projection)
    assert projection["no_alpha_assertion"]["candidate_rankings_present"] is False
    assert projection["no_alpha_assertion"]["economic_ranking_present"] is False
