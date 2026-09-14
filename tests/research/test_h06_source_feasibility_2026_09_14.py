from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/research/h06_source_feasibility_2026_09_14.py"
SPEC = importlib.util.spec_from_file_location("h06_source", SCRIPT)
assert SPEC and SPEC.loader
h06 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h06)
pytestmark = pytest.mark.research


def base_row() -> dict:
    return {"form_lineage_status": "ORIGINAL_ACTIVE", "derivative": False, "transaction_code": "P", "acquired_disposed": "A", "ownership_form": "D", "shares": 1.0, "price": 2.0, "owner_classification": "OPPORTUNISTIC", "identity_status": "IDENTITY_EXACT", "accepted_at_admissible": True, "available_before_decision": True, "ten_b5_status": "PASS_POST_COMPLIANCE_EXPLICIT_FALSE", "open_private_status": h06.OPEN_MARKET}


@pytest.mark.parametrize("change", [{"ownership_form": "I"}, {"derivative": True}, {"transaction_code": "S"}])
def test_wrong_ownership_derivative_or_direction_fails(change: dict) -> None:
    assert not h06.signal_admissible(base_row() | change)


def test_open_private_evidence_is_explicit_and_ambiguity_fails() -> None:
    assert h06.classify_open_private("purchased in open market transactions") == h06.OPEN_MARKET
    assert h06.classify_open_private("private placement purchase") == h06.PRIVATE
    assert h06.classify_open_private("purchase") == h06.AMBIGUOUS
    assert not h06.signal_admissible(base_row() | {"open_private_status": h06.AMBIGUOUS})
    assert not h06.signal_admissible(base_row() | {"open_private_status": h06.PRIVATE})


def test_ten_b5_policy_does_not_turn_pre_compliance_missing_into_false() -> None:
    assert h06.ten_b5_status(date(2023, 3, 31), None) == "10B5_1_DISCLOSURE_NOT_REQUIRED_AT_THE_TIME"
    assert h06.ten_b5_status(date(2023, 4, 1), True) == "EXCLUDE_10B5_1_CHECKED"
    assert h06.ten_b5_status(date(2023, 4, 1), False).startswith("PASS_")


def test_owner_classifier_uses_cik_and_only_prior_three_years() -> None:
    trades = [{"reporting_owner_cik": "1", "transaction_date": f"{year}-0{month}-02"} for year, month in ((2019, 1), (2020, 2), (2021, 3), (2022, 1))]
    assert h06.classify_owner("1", 2022, trades) == "OPPORTUNISTIC"
    assert h06.classify_owner("1", 2022, trades + [{"reporting_owner_cik": "1", "transaction_date": "2022-02-02"}]) == "OPPORTUNISTIC"
    assert h06.classify_owner("2", 2022, [{**row, "reporting_owner_name": "same"} for row in trades]) == "NON_PARTITIONABLE"
    assert h06.classify_owner("1", 2022, trades[:2]) == "NON_PARTITIONABLE"


def test_amendment_is_versioned_and_unresolved_fails_closed() -> None:
    assert h06.amendment_status("2024-02-02T12:00:00Z", "2024-02-01T12:00:00Z", True, amendment=True) == "ORIGINAL_ACTIVE"
    assert h06.amendment_status("2024-02-02T12:00:00Z", "2024-02-03T12:00:00Z", False, amendment=True) == "AMENDMENT_LINEAGE_UNRESOLVED"


def test_current_ticker_alone_is_not_continuity() -> None:
    assert h06.identity_status(cik_interval_matches=True, common_equity=True, ticker_only=True) == "IDENTITY_AMBIGUOUS"


def test_probe_and_module_have_no_economic_surface() -> None:
    receipt = json.loads((ROOT / "data/research_manifests/h06_source_probe_receipt_2026_09_14.json").read_text())
    assert not h06.contains_economic_fields(receipt)
    source = SCRIPT.read_text()
    assert "backtest" not in source.casefold()
    assert "run_equity_local_diagnostics" not in source


def test_frozen_manifests_self_hash_and_boundary() -> None:
    for name in ("h06_routine_classifier_v1_2026_09_14.json", "h06_signal_transaction_taxonomy_v1_2026_09_14.json"):
        value = json.loads((ROOT / "data/research_manifests" / name).read_text())
        assert value["self_sha256"] == h06.self_hash(value)
    feasibility = json.loads((ROOT / "data/research_manifests/h06_source_feasibility_2026_09_14.json").read_text())
    assert feasibility["status"] == "H06_BLOCKED_MULTIPLE_CAUSAL_REQUIREMENTS"
    assert all(value is False for value in feasibility["boundaries"].values())


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"cik_interval_matches": True, "common_equity": True}, "IDENTITY_EXACT"),
        ({"cik_interval_matches": False, "common_equity": True}, "IDENTITY_MISSING"),
        ({"cik_interval_matches": True, "common_equity": False}, "NON_COMMON_EQUITY"),
    ],
)
def test_identity_interval_and_share_class_cases(kwargs: dict, expected: str) -> None:
    assert h06.identity_status(**kwargs) == expected
