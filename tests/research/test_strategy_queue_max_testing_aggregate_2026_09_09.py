import json
import os
import subprocess
import sys

from scripts.research import build_strategy_queue_max_testing_aggregate_2026_09_09 as aggregate


def test_34_primary_ids_and_category_counts() -> None:
    candidate = json.loads((aggregate.OUT / "candidate_verdicts.json").read_text(encoding="utf-8"))
    result = json.loads((aggregate.OUT / "aggregate_results.json").read_text(encoding="utf-8"))
    assert candidate["candidate_ids"] == aggregate.EXPECTED_IDS
    assert len(candidate["records"]) == 34
    assert {item["candidate_id"] for item in candidate["records"]} == set(aggregate.EXPECTED_IDS)
    assert all(item["candidate_id"] != "H14C" for item in candidate["records"])
    assert result["counts"] == aggregate.aggregate_counts(candidate["records"])
    assert result["counts"]["primary_candidates"] == 34
    assert result["counts"]["integration_ready"] == 0
    assert result["counts"]["globally_completely_rejected"] == 0


def test_corrected_h14_and_source_review_pins() -> None:
    candidate = json.loads((aggregate.OUT / "candidate_verdicts.json").read_text(encoding="utf-8"))
    h14 = next(item for item in candidate["records"] if item["candidate_id"] == "H14")
    assert h14["complement"]["fixed_95_5"] == "TESTED_INFEASIBLE_DIAGNOSTIC"
    assert "31 active days/14 months" in h14["costs_caps_statistics"]
    assert "0.004392" in h14["costs_caps_statistics"]
    assert "0.003236" in h14["costs_caps_statistics"]
    assert "0.008274" in h14["costs_caps_statistics"]
    report = aggregate.REPORT.read_text(encoding="utf-8")
    assert "EQUITY_INDEPENDENT_REVIEW_PASS" in report
    assert "CRYPTO_MAX_TESTING_COMPLETE_V3" in report
    assert "CROSSASSET_MAX_TESTING_COMPLETE_V4" in report
    aggregate.verify_source_provenance()
    provenance = json.loads((aggregate.OUT / "aggregate_results.json").read_text(encoding="utf-8"))["source_provenance"]["source_artifacts"]
    assert provenance["data/research_runs/crypto_max_testing_2026_09_09/diagnostics/diagnostic_results.json"]["canonical_sha256"].startswith("b001fc2a")
    assert provenance["data/research_runs/crypto_max_testing_2026_09_09/capacity_diagnostics/capacity_diagnostic_results.json"]["canonical_sha256"].startswith("5b217b17")
    assert provenance["data/research_runs/crossasset_max_testing_2026_09_09_v3/verdict_matrix.json"]["canonical_sha256"].startswith("f12f7fa3")
    assert provenance["data/research_runs/crossasset_max_testing_2026_09_09_v3/overlay_diagnostic_results.json"]["canonical_sha256"].startswith("c489e276")


def test_no_false_promotion_or_private_markers() -> None:
    candidate = json.loads((aggregate.OUT / "candidate_verdicts.json").read_text(encoding="utf-8"))
    aggregate_result = json.loads((aggregate.OUT / "aggregate_results.json").read_text(encoding="utf-8"))
    assert candidate["economic_run"] is False and candidate["promotion"] is False
    assert aggregate_result["economic_run"] is False and aggregate_result["promotion"] is False
    assert aggregate_result["v3_replacement"] is False
    text = (aggregate.REPORT).read_text(encoding="utf-8")
    assert "NEXT_PUBLIC_PROBE" not in text
    assert "BLOCKED_RETRY_REQUIRED" not in text
    assert "@" not in text


def test_verifier_is_arbitrary_cwd_read_only() -> None:
    tracked = [aggregate.OUT / name for name in ("candidate_verdicts.json", "aggregate_results.json", "manifest.json", "receipt.json")]
    tracked.append(aggregate.REPORT)
    before = {path: path.stat().st_mtime_ns for path in tracked}
    completed = subprocess.run(
        [sys.executable, str(aggregate.REPO / "scripts/research/build_strategy_queue_max_testing_aggregate_2026_09_09.py"), "--verify"],
        cwd="/tmp",
        check=True,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
    )
    assert json.loads(completed.stdout)["status"] == "AGGREGATE_MAX_TESTING_VERIFIED"
    assert before == {path: path.stat().st_mtime_ns for path in tracked}


def test_production_config_trade_scope_is_unchanged_by_verification() -> None:
    def snapshot() -> dict[str, tuple[int, str]]:
        return {
            relative: ((aggregate.REPO / relative).stat().st_mtime_ns, aggregate.physical_hash(aggregate.REPO / relative))
            for relative in aggregate.PRODUCTION_SCOPE
        }

    before = snapshot()
    assert aggregate.verify()["read_only"] is True
    assert before == snapshot()
