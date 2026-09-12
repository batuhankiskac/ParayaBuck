from __future__ import annotations

import json
from pathlib import Path

from scripts.research import report_test_performance as report


def test_parse_durations_keeps_exact_nodeids_and_phases() -> None:
    durations, summary = report.parse_durations(
        """
        0.03s setup    tests/test_alpha.py::test_one[param]
        1.20s call     tests/test_alpha.py::test_one[param]
        0.04s teardown  tests/test_alpha.py::test_one[param]
        ============== 2 passed, 1 skipped in 1.35s ==============
        """
    )

    assert [item.as_dict() for item in durations] == [
        {"nodeid": "tests/test_alpha.py::test_one[param]", "seconds": 0.03, "phase": "setup"},
        {"nodeid": "tests/test_alpha.py::test_one[param]", "seconds": 1.2, "phase": "call"},
        {"nodeid": "tests/test_alpha.py::test_one[param]", "seconds": 0.04, "phase": "teardown"},
    ]
    assert summary == {"passed": 2, "skipped": 1, "wall_seconds": 1.35}


def test_parse_marks_pytest_hidden_subthreshold_durations_as_partial() -> None:
    _, summary = report.parse_durations(
        "2 passed in 0.10s\n(12 durations < 0.005s hidden.  Use -vv to show these durations.)\n"
    )

    assert summary["duration_coverage"] == "partial"


def test_report_is_stable_and_marks_small_slow_and_deep_without_gating() -> None:
    durations = [
        report.Duration("tests/test_small.py::test_small", 0.02, "call"),
        report.Duration("tests/research/test_deep.py::test_deep", 2.0, "call"),
        report.Duration("tests/test_medium.py::test_medium", 0.5, "call"),
    ]
    value = report.build_report(
        durations,
        {"passed": 3, "wall_seconds": 2.52},
        threshold_seconds=1.0,
        small_seconds=0.1,
        category_rules=[{"pattern": "tests/research/*", "category": "deep"}],
    )

    assert value["benchmark_status"] == "complete"
    assert value["advisory_only"] is True
    assert [row["advisory_category"] for row in value["nodes"]] == ["deep", "medium", "small"]
    assert value["findings"]["slow"] == [
        {
            "nodeid": "tests/research/test_deep.py::test_deep",
            "total_seconds": 2.0,
            "category": "deep",
            "advisory_category": "deep",
            "phases": [{"nodeid": "tests/research/test_deep.py::test_deep", "seconds": 2.0, "phase": "call"}],
        }
    ]
    neutral = report.build_report(
        [
            report.Duration("tests/test_research_runner.py::test_current", 0.1, "call"),
            report.Duration("tests/research/test_historical.py::test_closed", 0.1, "call"),
        ],
        {"passed": 2, "wall_seconds": 0.2},
    )
    assert {row["category"] for row in neutral["nodes"]} == {"unclassified"}


def test_baseline_reports_new_slow_and_category_crossing() -> None:
    value = report.build_report(
        [report.Duration("tests/test_one.py::test_case", 1.5, "call")],
        {"passed": 1, "wall_seconds": 1.5},
        threshold_seconds=1.0,
        category_rules=[{"pattern": "tests/*", "category": "fast"}],
        baseline={
            "nodes": [
                {
                    "nodeid": "tests/test_one.py::test_case",
                    "seconds": 0.2,
                    "category": "small",
                }
            ]
        },
    )

    assert [item["nodeid"] for item in value["findings"]["new_slow"]] == ["tests/test_one.py::test_case"]
    assert value["findings"]["category_crossings"] == [
        {
            "nodeid": "tests/test_one.py::test_case",
            "from": "small",
            "to": "slow",
            "total_seconds": 1.5,
        }
    ]


def test_slow_category_aggregates_setup_call_and_teardown() -> None:
    nodeid = "tests/test_case.py::test_case"
    value = report.build_report(
        [
            report.Duration(nodeid, 0.6, "setup"),
            report.Duration(nodeid, 0.3, "call"),
            report.Duration(nodeid, 0.2, "teardown"),
        ],
        {"passed": 1, "wall_seconds": 1.1},
        threshold_seconds=1.0,
    )

    assert value["nodes"][0]["total_seconds"] == 1.1
    assert value["nodes"][0]["advisory_category"] == "slow"
    assert len(value["nodes"][0]["phases"]) == 3
    assert value["findings"]["slow"][0]["total_seconds"] == 1.1


def test_metadata_and_static_source_warnings_are_heuristic_with_file_line_clues(tmp_path: Path) -> None:
    source = tmp_path / "tests" / "test_fast.py"
    source.parent.mkdir()
    source.write_text(
        "def test_case():\n    payload = (root / 'data/historical/big.csv').read_bytes()\n",
        encoding="utf-8",
    )
    nodeid = "tests/test_fast.py::test_case"
    value = report.build_report(
        [report.Duration(nodeid, 0.2, "call")],
        {"passed": 1, "wall_seconds": 0.2},
        metadata={nodeid: {"fixture_bytes": 2_000, "source_load_bytes": 3_000, "source_load_files": 2}},
        fixture_bytes_threshold=1_000,
        source_load_bytes_threshold=2_000,
        repo_root=tmp_path,
        category_rules=[{"pattern": "tests/test_fast.py*", "category": "fast"}],
    )

    kinds = {item["kind"] for item in value["findings"]["metadata_warnings"]}
    assert kinds == {"oversized_fixture", "oversized_source_load"}
    clues = value["findings"]["static_source_clues"]
    assert clues and clues[0]["clue"] == "tests/test_fast.py:2"
    assert clues[0]["heuristic"] is True


def test_missing_summary_or_nonzero_exit_never_claims_success() -> None:
    incomplete = report.build_report(
        [report.Duration("tests/test_case.py::test_case", 0.1, "call")],
        {},
        run_exit_code=0,
    )
    failed = report.build_report(
        [report.Duration("tests/test_case.py::test_case", 0.1, "call")],
        {"failed": 1},
        run_exit_code=1,
    )

    assert incomplete["benchmark_status"] == "incomplete"
    assert failed["benchmark_status"] == "failed"


def test_pass_summary_without_call_records_is_incomplete() -> None:
    value = report.build_report([], {"passed": 2, "wall_seconds": 0.2})

    assert value["benchmark_status"] == "incomplete"
    assert value["summary"]["duration_coverage"] == "partial"


def test_json_is_machine_stable(tmp_path: Path) -> None:
    value = report.build_report(
        [report.Duration("tests/test.py::test_case", 0.4, "call")],
        {"passed": 1, "wall_seconds": 0.4},
    )
    path = tmp_path / "report.json"
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == value


def test_cli_preserves_captured_pytest_exit_code(tmp_path: Path) -> None:
    source = tmp_path / "pytest.out"
    output = tmp_path / "report.json"
    source.write_text("1 failed in 0.20s\n", encoding="utf-8")

    exit_code = report.main([str(source), "--exit-code", "7", "--output-json", str(output)])

    assert exit_code == 7
    assert json.loads(output.read_text(encoding="utf-8"))["benchmark_status"] == "failed"
