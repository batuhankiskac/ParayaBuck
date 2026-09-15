from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.research import h06_offline_core_population_2026_09_15 as subject


def _core_gates(**overrides: bool) -> dict[str, bool]:
    values = {
        "lineage": True,
        "nonderivative": True,
        "purchase": True,
        "acquired": True,
        "direct": True,
        "shares": True,
        "price": True,
        "opportunistic": True,
        "timing": True,
        "ten_b5": True,
        "open_market": True,
    }
    values.update(overrides)
    return values


def test_builder_has_no_network_import_or_url() -> None:
    source = Path(subject.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(name=node.module or "")])
    }
    assert imports.isdisjoint({"requests", "urllib", "httpx", "aiohttp"})
    assert "http://" not in source and "https://" not in source


@pytest.mark.research_deep(reason="full frozen H06 source-cache custody assurance")
def test_accepted_source_hashes_and_counts() -> None:
    custody = subject.verify_source_custody()
    assert custody["quarter_sources"] == 30
    assert custody["july_bodies"] == 11242
    assert custody["acceptance_map_rows"] == 395310
    assert custody["network_used"] is False


def test_quarterly_schema_keys_and_aff10b5_variant() -> None:
    receipt = json.loads(subject.SCHEMA_RECEIPT.read_text())
    subject.verify_seal(receipt)
    assert receipt["status"] == "SEC_BULK_SCHEMA_KEYS_PROVEN"
    assert receipt["keys"]["transaction"] == ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK"]
    assert receipt["header_variant_counts"]["SUBMISSION.tsv"] == 2


def test_july_and_quarterly_share_one_canonical_observation_schema() -> None:
    manifest = json.loads(subject.CORE_MANIFEST.read_text())
    schema = manifest["logical_tables"]["normalized_relevant_transactions"]["schema"]
    assert "source_kind" in schema
    assert manifest["normalization"]["july_transaction_rows"] > 0


def test_joint_owner_canaries_prove_exact_expansion() -> None:
    receipt = json.loads(subject.JOINT_RECEIPT.read_text())
    assert receipt["status"] == "JOINT_OWNER_TRANSACTION_EXPANSION_PROVEN"
    assert receipt["canaries"]
    assert all(row["actual_expanded_rows"] == row["expected_expanded_rows"] for row in receipt["canaries"])
    assert "actor inference" in receipt["mapping_rule"]


def test_footnote_canaries_resolve_exact_field_references() -> None:
    receipt = json.loads(subject.FOOTNOTE_RECEIPT.read_text())
    assert receipt["status"] == "SEC_BULK_TRANSACTION_FOOTNOTE_LINKAGE_PROVEN"
    assert receipt["canaries"]
    assert all(row["referenced_ids"] == row["resolved_ids"] for row in receipt["canaries"])


def test_stable_transaction_owner_id_is_source_identity_only() -> None:
    database = sqlite3.connect(subject.POPULATION / "h06_population.sqlite3")
    row = database.execute(
        "SELECT row_id,accession,source_transaction_id,reporting_owner_cik FROM normalized_relevant_transactions LIMIT 1"
    ).fetchone()
    database.close()
    import hashlib

    assert row[0] == hashlib.sha256(f"{row[1]}|TABLE_I|{row[2]}|{row[3]}".encode()).hexdigest()


def test_owner_name_is_not_in_owner_year_schema() -> None:
    manifest = json.loads(subject.CORE_MANIFEST.read_text())
    schema = manifest["logical_tables"]["owner_year_classification"]["schema"]
    assert "reporting_owner_cik" in schema
    assert all("name" not in field for field in schema)


def test_unamended_original_and_unresolved_amendment_are_present() -> None:
    receipt = json.loads(subject.LINEAGE_RECEIPT.read_text())
    assert receipt["originals"] > 0
    assert receipt["unresolved_amendments"] > 0
    assert "fail closed" in receipt["unresolved_policy"]


def test_amendment_intervals_are_not_timeless_overwrites() -> None:
    database = sqlite3.connect(subject.POPULATION / "h06_population.sqlite3")
    row = database.execute(
        "SELECT version_start,version_end FROM lineage_versions WHERE lineage_status='SUPERSEDED_AFTER_AMENDMENT' LIMIT 1"
    ).fetchone()
    database.close()
    assert row and row[0] < row[1]


@pytest.mark.parametrize(
    ("owner_class", "gates", "ten_b5", "open_private", "expected"),
    [
        ("OPPORTUNISTIC", {"lineage": False}, "PASS", "OPEN_MARKET_EXPLICIT", "EXCLUDE_LINEAGE"),
        ("OPPORTUNISTIC", {"nonderivative": False}, "PASS", "OPEN_MARKET_EXPLICIT", "EXCLUDE_DERIVATIVE"),
        ("OPPORTUNISTIC", {"purchase": False}, "PASS", "OPEN_MARKET_EXPLICIT", "EXCLUDE_NOT_PURCHASE_CODE"),
        ("OPPORTUNISTIC", {"acquired": False}, "PASS", "OPEN_MARKET_EXPLICIT", "EXCLUDE_NOT_ACQUIRED"),
        ("OPPORTUNISTIC", {"direct": False}, "PASS", "OPEN_MARKET_EXPLICIT", "EXCLUDE_INDIRECT"),
        ("OPPORTUNISTIC", {"shares": False}, "PASS", "OPEN_MARKET_EXPLICIT", "EXCLUDE_INVALID_SHARES"),
        ("OPPORTUNISTIC", {"price": False}, "PASS", "OPEN_MARKET_EXPLICIT", "EXCLUDE_INVALID_PRICE"),
        ("ROUTINE", {"opportunistic": False}, "PASS", "OPEN_MARKET_EXPLICIT", "EXCLUDE_OWNER_ROUTINE"),
        ("NON_PARTITIONABLE", {"opportunistic": False}, "PASS", "OPEN_MARKET_EXPLICIT", "EXCLUDE_OWNER_NON_PARTITIONABLE"),
        ("OPPORTUNISTIC", {"timing": False}, "PASS", "OPEN_MARKET_EXPLICIT", "EXCLUDE_TIMING"),
        ("OPPORTUNISTIC", {"ten_b5": False}, "EXCLUDE_10B5_1_CHECKED", "OPEN_MARKET_EXPLICIT", "EXCLUDE_10B5_1"),
        ("OPPORTUNISTIC", {"open_market": False}, "PASS", "PRIVATE_PURCHASE_EXPLICIT", "EXCLUDE_PRIVATE"),
        ("OPPORTUNISTIC", {"open_market": False}, "PASS", "P_PURCHASE_AMBIGUOUS", "EXCLUDE_OPEN_PRIVATE_AMBIGUOUS"),
    ],
)
def test_primary_core_exclusions(
    owner_class: str, gates: dict[str, bool], ten_b5: str, open_private: str, expected: str
) -> None:
    assert subject._core_primary(_core_gates(**gates), owner_class, ten_b5, open_private) == expected


def test_all_core_gates_pass_without_identity_promotion() -> None:
    assert subject._core_primary(_core_gates(), "OPPORTUNISTIC", "PASS", "OPEN_MARKET_EXPLICIT") is None
    database = sqlite3.connect(subject.POPULATION / "h06_population.sqlite3")
    statuses = {row[0] for row in database.execute("SELECT DISTINCT identity_precheck FROM signal_core_gates")}
    database.close()
    assert "IDENTITY_EXACT" not in statuses


def test_identity_precheck_counts_are_scoped_to_core_pass_rows() -> None:
    manifest = json.loads(subject.CORE_MANIFEST.read_text())
    assert sum(manifest["identity_precheck"].values()) == manifest["signal_gate_counts"]["primary_exclusions"]["CORE_PASS"]


def test_post_compliance_10b5_states_are_fail_closed() -> None:
    assert subject.policy.ten_b5_status("2024-01-01", True) == "EXCLUDE_10B5_1_CHECKED"
    assert subject.policy.ten_b5_status("2024-01-01", False) == "PASS_POST_COMPLIANCE_EXPLICIT_FALSE"
    assert subject.policy.ten_b5_status("2024-01-01", None) == "EXCLUDE_POST_COMPLIANCE_DISCLOSURE_MISSING"


def test_pre_compliance_missing_does_not_pass_but_explicit_non_plan_can() -> None:
    assert not subject.policy.ten_b5_status("2022-01-01", None).startswith("PASS_")
    assert subject.policy.ten_b5_status("2022-01-01", None, "not pursuant to a Rule 10b5-1 plan").startswith("PASS_")


def test_private_language_has_priority_and_ambiguous_p_fails() -> None:
    assert subject.policy.open_private_status("open market purchase in a private transaction") == "PRIVATE_PURCHASE_EXPLICIT"
    assert subject.policy.open_private_status("") == "P_PURCHASE_AMBIGUOUS"


def test_owner_year_invariants_and_year_y_exclusion() -> None:
    rows = [
        {"row_id": "a", "reporting_owner_cik": "1", "transaction_date": "2019-01-01", "transaction_code": "P", "non_derivative": True, "available_at": "2019-01-02T00:00:00Z", "lineage_status": "ORIGINAL_ACTIVE"},
        {"row_id": "b", "reporting_owner_cik": "1", "transaction_date": "2020-02-01", "transaction_code": "S", "non_derivative": True, "available_at": "2020-02-02T00:00:00Z", "lineage_status": "ORIGINAL_ACTIVE"},
        {"row_id": "c", "reporting_owner_cik": "1", "transaction_date": "2021-03-01", "transaction_code": "P", "non_derivative": True, "available_at": "2021-03-02T00:00:00Z", "lineage_status": "ORIGINAL_ACTIVE"},
        {"row_id": "y", "reporting_owner_cik": "1", "transaction_date": "2022-01-01", "transaction_code": "P", "non_derivative": True, "available_at": "2022-01-02T00:00:00Z", "lineage_status": "ORIGINAL_ACTIVE"},
    ]
    result = subject.policy.classify_owner_year(rows, "1", 2022, "2022-01-01T05:00:00Z")
    assert result["classification"] == "OPPORTUNISTIC"
    assert "y" not in result["input_transaction_ids"]


def test_decision_calendar_is_exact_55_months_and_21_sessions() -> None:
    database = sqlite3.connect(subject.POPULATION / "h06_population.sqlite3")
    rows = database.execute("SELECT calendar_month,decision_session,prior_21_sessions FROM monthly_decisions ORDER BY calendar_month").fetchall()
    database.close()
    assert len(rows) == 55
    assert rows[0][:2] == ("2022-01", "2022-01-31")
    assert rows[-1][:2] == ("2026-07", "2026-07-31")
    assert all(len(json.loads(row[2])) == 21 for row in rows)


def test_information_session_window_boundaries() -> None:
    decisions = [{"decision_session": "2024-02-29", "prior_21_sessions": ["2024-02-01", "2024-02-29"]}]
    assert subject.policy.map_to_decisions("2024-02-01", decisions) == ["2024-02-29"]
    assert subject.policy.map_to_decisions("2024-01-31", decisions) == []
    assert subject.policy.map_to_decisions("2024-03-01", decisions) == []


def test_enrichment_targets_are_deduplicated_and_request_raw_sip() -> None:
    database = sqlite3.connect(subject.POPULATION / "h06_population.sqlite3")
    cover = database.execute("SELECT COUNT(*),COUNT(DISTINCT issuer_cik||'|'||form4_symbol||'|'||decision_timestamp) FROM cover_enrichment_targets").fetchone()
    raw = database.execute("SELECT DISTINCT timeframe,feed,adjustment FROM raw_sip_targets").fetchall()
    database.close()
    assert cover[0] == cover[1]
    assert raw == [("1Day", "SIP", "raw")]


def test_cover_targets_come_from_local_submissions_metadata() -> None:
    database = sqlite3.connect(subject.POPULATION / "h06_population.sqlite3")
    assert database.execute("SELECT COUNT(*) FROM cover_enrichment_targets WHERE cover_accession IS NULL").fetchone()[0] == 0
    assert database.execute("SELECT COUNT(*) FROM cover_enrichment_targets WHERE cover_form NOT IN ('10-K','10-Q')").fetchone()[0] == 0
    database.close()


def test_logical_rebuild_hashes_match_for_every_required_table() -> None:
    manifest = json.loads(subject.CORE_MANIFEST.read_text())
    assert manifest["determinism"]["all_match"] is True
    assert set(manifest["determinism"]["run_a"]) == set(subject.TABLE_SORTS)
    assert manifest["determinism"]["run_a"] == manifest["determinism"]["run_b"]


def test_holdout_has_no_outcome_or_economic_fields() -> None:
    receipt = json.loads(subject.HOLDOUT_RECEIPT.read_text())
    assert receipt["signal_rows_processed"] > 0
    assert receipt["outcome_fields_present"] is False
    assert receipt["economics_accessed"] is False
    assert receipt["status"] == "TRUE_HOLDOUT_SIGNAL_SIDE_ONLY_NO_ECONOMIC_ACCESS"


@pytest.mark.parametrize("forbidden", ["score", "ranking", "returns", "pnl", "sharpe", "drawdown", "backtest"])
def test_no_economic_callable_surface(forbidden: str) -> None:
    assert not hasattr(subject, forbidden)


def test_all_tracked_core_artifacts_are_self_hashed() -> None:
    assert subject.verify_tracked()["status"] == "H06_OFFLINE_CORE_POPULATION_VERIFIED"
