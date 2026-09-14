from __future__ import annotations

import json

import pytest

from scripts.research import h06_full_structural_admission_2026_09_14 as subject


def version(accession: str, available: str, resolved: bool = True) -> dict[str, object]:
    return {"accession": accession, "available_at": available, "lineage_resolved": resolved}


def history(row_id: str, owner: str, day: str, code: str = "P", available: str = "2021-12-01T00:00:00Z", lineage: str = "ORIGINAL_ACTIVE") -> dict[str, object]:
    return {"row_id": row_id, "reporting_owner_cik": owner, "transaction_date": day, "transaction_code": code, "non_derivative": True, "available_at": available, "lineage_status": lineage}


def passing_row() -> dict[str, object]:
    return {"lineage_status": "ORIGINAL_ACTIVE", "non_derivative": True, "transaction_code": "P", "acquired_disposed": "A", "ownership_form": "D", "shares": "10", "price": "5", "owner_classification": "OPPORTUNISTIC", "identity_status": "IDENTITY_EXACT", "accepted_at": "2024-01-01T00:00:00Z", "visible_by_decision": True, "ten_b5_status": "PASS_POST_COMPLIANCE_EXPLICIT_FALSE", "open_private_status": "OPEN_MARKET_EXPLICIT", "denominator_status": "EXACT"}


def test_original_before_amendment_remains_historically_visible() -> None:
    rows = [version("o", "2024-01-01T00:00:00Z"), version("a", "2024-02-01T00:00:00Z")]
    assert subject.active_version(rows, "2024-01-31T00:00:00Z")["accession"] == "o"


def test_amendment_changes_future_only() -> None:
    rows = [version("o", "2024-01-01T00:00:00Z"), version("a", "2024-02-01T00:00:00Z")]
    assert subject.active_version(rows, "2024-02-02T00:00:00Z")["accession"] == "a"


def test_unresolved_amendment_fails_closed() -> None:
    assert subject.active_version([version("a", "2024-01-01T00:00:00Z", False)], "2024-02-01T00:00:00Z") is None


def test_future_amendment_cannot_rewrite_prior_classification() -> None:
    rows = [history("a", "1", "2019-01-01"), history("b", "1", "2020-02-01"), history("c", "1", "2021-03-01"), history("d", "1", "2021-04-01", available="2022-02-01T00:00:00Z", lineage="AMENDED_VERSION_ACTIVE")]
    result = subject.classify_owner_year(rows, "1", 2022, "2022-01-01T05:00:00Z")
    assert "d" not in result["input_transaction_ids"]


def test_p_and_s_both_establish_history() -> None:
    rows = [history("a", "1", "2019-01-01", "P"), history("b", "1", "2020-02-01", "S"), history("c", "1", "2021-03-01", "P")]
    assert subject.classify_owner_year(rows, "1", 2022, "2022-01-01T05:00:00Z")["classification"] == "OPPORTUNISTIC"


def test_owner_cik_only() -> None:
    rows = [history("a", "1", "2019-01-01"), history("b", "2", "2020-01-01"), history("c", "1", "2021-01-01")]
    assert subject.classify_owner_year(rows, "1", 2022, "2022-01-01T05:00:00Z")["classification"] == "NON_PARTITIONABLE"


def test_missing_prior_year_is_non_partitionable() -> None:
    rows = [history("a", "1", "2019-01-01"), history("c", "1", "2021-01-01")]
    assert subject.classify_owner_year(rows, "1", 2022, "2022-01-01T05:00:00Z")["classification"] == "NON_PARTITIONABLE"


def test_common_month_in_three_years_is_routine() -> None:
    rows = [history(str(y), "1", f"{y}-02-01") for y in (2019, 2020, 2021)]
    assert subject.classify_owner_year(rows, "1", 2022, "2022-01-01T05:00:00Z")["classification"] == "ROUTINE"


def test_no_common_month_is_opportunistic() -> None:
    rows = [history("a", "1", "2019-01-01"), history("b", "1", "2020-02-01"), history("c", "1", "2021-03-01")]
    assert subject.classify_owner_year(rows, "1", 2022, "2022-01-01T05:00:00Z")["classification"] == "OPPORTUNISTIC"


def test_year_y_trade_cannot_affect_year_y() -> None:
    rows = [history("a", "1", "2019-01-01"), history("b", "1", "2020-02-01"), history("c", "1", "2021-03-01"), history("y", "1", "2022-01-01")]
    assert "y" not in subject.classify_owner_year(rows, "1", 2022, "2022-01-01T05:00:00Z")["input_transaction_ids"]


def test_late_reported_prior_year_trade_is_excluded() -> None:
    rows = [history("a", "1", "2019-01-01"), history("b", "1", "2020-02-01"), history("c", "1", "2021-03-01", available="2022-01-02T00:00:00Z")]
    assert subject.classify_owner_year(rows, "1", 2022, "2022-01-01T05:00:00Z")["classification"] == "NON_PARTITIONABLE"


def test_p_a_direct_passes_basic_direction_gate() -> None:
    assert subject.signal_gate(passing_row())["admission_status"] == "STRUCTURALLY_ADMITTED_H06_TRANSACTION"


@pytest.mark.parametrize(("field", "value", "exclusion"), [("transaction_code", "S", "EXCLUDE_NOT_PURCHASE_CODE"), ("ownership_form", "I", "EXCLUDE_INDIRECT"), ("non_derivative", False, "EXCLUDE_DERIVATIVE")])
def test_direction_ownership_and_derivative_exclusions(field: str, value: object, exclusion: str) -> None:
    row = passing_row()
    row[field] = value
    assert subject.signal_gate(row)["primary_exclusion"] == exclusion


def test_private_excluded() -> None:
    row = passing_row()
    row["open_private_status"] = "PRIVATE_PURCHASE_EXPLICIT"
    assert subject.signal_gate(row)["primary_exclusion"] == "EXCLUDE_PRIVATE"


def test_ambiguous_p_excluded() -> None:
    row = passing_row()
    row["open_private_status"] = "P_PURCHASE_AMBIGUOUS"
    assert subject.signal_gate(row)["primary_exclusion"] == "EXCLUDE_OPEN_PRIVATE_AMBIGUOUS"


def test_private_language_wins_conflict() -> None:
    assert subject.open_private_status("open market purchase later completed as a privately negotiated transaction") == "PRIVATE_PURCHASE_EXPLICIT"


def test_post_2023_10b5_true_excluded() -> None:
    assert subject.ten_b5_status("2024-01-01", True) == "EXCLUDE_10B5_1_CHECKED"


def test_post_2023_explicit_false_may_pass() -> None:
    assert subject.ten_b5_status("2024-01-01", False).startswith("PASS_")


def test_post_2023_missing_excluded() -> None:
    assert subject.ten_b5_status("2024-01-01", None) == "EXCLUDE_POST_COMPLIANCE_DISCLOSURE_MISSING"


def test_pre_2023_missing_alone_cannot_pass() -> None:
    assert not subject.ten_b5_status("2022-01-01", None).startswith("PASS_")


def test_pre_2023_explicit_non_plan_may_pass() -> None:
    assert subject.ten_b5_status("2022-01-01", None, "not pursuant to a Rule 10b5-1 plan").startswith("PASS_")


def test_routine_owner_excluded() -> None:
    row = passing_row()
    row["owner_classification"] = "ROUTINE"
    assert subject.signal_gate(row)["primary_exclusion"] == "EXCLUDE_OWNER_ROUTINE"


def test_non_partitionable_owner_excluded() -> None:
    row = passing_row()
    row["owner_classification"] = "NON_PARTITIONABLE"
    assert subject.signal_gate(row)["primary_exclusion"] == "EXCLUDE_OWNER_NON_PARTITIONABLE"


def test_exact_single_class_passes() -> None:
    assert subject.exact_identity(cik_match=True, class_count=1, symbol_match=True, common_equity=True, lifetime_count=1) == "IDENTITY_EXACT"


def test_ticker_ambiguity_fails_closed() -> None:
    assert subject.exact_identity(cik_match=True, class_count=1, symbol_match=False, common_equity=True, lifetime_count=1) == "IDENTITY_AMBIGUOUS"


def test_multi_class_ambiguity_fails_closed() -> None:
    assert subject.exact_identity(cik_match=True, class_count=2, symbol_match=True, common_equity=True, lifetime_count=1) == "IDENTITY_AMBIGUOUS"


def test_non_common_excluded() -> None:
    assert subject.exact_identity(cik_match=True, class_count=1, symbol_match=True, common_equity=False, lifetime_count=1) == "NON_COMMON_EQUITY"


def test_future_security_state_cannot_rewrite_prior_decision() -> None:
    assert subject.active_version([version("old", "2024-01-01T00:00:00Z"), version("new", "2025-01-01T00:00:00Z")], "2024-06-01T00:00:00Z")["accession"] == "old"


def test_exact_shares_fact_selection() -> None:
    rows = [{"shares": "10", "instant": "2024-01-01", "available_at": "2024-01-02T00:00:00Z", "exact_context": True}]
    assert subject.select_shares_fact(rows, "2024-02-01T00:00:00Z")["shares"] == "10"


def test_future_fact_excluded() -> None:
    rows = [{"shares": "10", "instant": "2024-03-01", "available_at": "2024-01-02T00:00:00Z", "exact_context": True}]
    assert subject.select_shares_fact(rows, "2024-02-01T00:00:00Z") is None


def test_future_filing_excluded() -> None:
    rows = [{"shares": "10", "instant": "2024-01-01", "available_at": "2024-03-02T00:00:00Z", "exact_context": True}]
    assert subject.select_shares_fact(rows, "2024-02-01T00:00:00Z") is None


def test_conflicting_context_excluded() -> None:
    rows = [{"shares": "10", "instant": "2024-01-01", "available_at": "2024-01-02T00:00:00Z", "exact_context": True}, {"shares": "11", "instant": "2024-01-01", "available_at": "2024-01-02T00:00:00Z", "exact_context": True}]
    assert subject.select_shares_fact(rows, "2024-02-01T00:00:00Z") is None


def test_split_between_fact_and_price_blocks() -> None:
    assert subject.denominator_status("10", "5", intervening_split=True) == "SPLIT_INTERVENING_BLOCKED"


def test_raw_sip_bar_exact() -> None:
    assert subject.denominator_status("10", "5", intervening_split=False) == "EXACT"


def test_missing_raw_bar_fails_closed() -> None:
    assert subject.denominator_status("10", None, intervening_split=False) == "MISSING_RAW_PRICE"


def test_unavailable_transaction_excluded_from_earlier_decision() -> None:
    decisions = [{"decision_session": "2024-02-29", "prior_21_sessions": ["2024-02-01"]}]
    assert subject.map_to_decisions("2024-03-01", decisions) == []


def test_visible_transaction_may_enter_later_window() -> None:
    decisions = [{"decision_session": "2024-02-29", "prior_21_sessions": ["2024-02-01"]}]
    assert subject.map_to_decisions("2024-02-01", decisions) == ["2024-02-29"]


def test_outside_21_session_window_absent() -> None:
    decisions = [{"decision_session": "2024-02-29", "prior_21_sessions": ["2024-02-02"]}]
    assert subject.map_to_decisions("2024-02-01", decisions) == []


@pytest.mark.parametrize("forbidden", ["score", "ranking", "return", "portfolio", "pnl", "sharpe", "decile"])
def test_module_has_no_economic_surface(forbidden: str) -> None:
    assert not hasattr(subject, forbidden)


def test_true_holdout_economics_inaccessible() -> None:
    value = json.loads(subject.HOLDOUT.read_text())
    assert value["economics_accessed"] is False and value["outcome_fields_present"] is False


def test_all_tracked_structural_artifacts_are_self_hashed() -> None:
    for path in (subject.MANIFEST, subject.OWNER_MANIFEST, subject.RECEIPT, subject.HOLDOUT):
        subject.verify_seal(json.loads(path.read_text()))
