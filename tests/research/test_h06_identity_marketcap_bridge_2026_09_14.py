from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tests.research_testkit import canonical_hash

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/research/h06_identity_marketcap_bridge_2026_09_14.py"
SPEC = importlib.util.spec_from_file_location("h06_bridge", SCRIPT)
assert SPEC and SPEC.loader
h06 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h06)
pytestmark = pytest.mark.research


def test_recovery_receipt_nested_self_hash() -> None:
    receipt = json.loads((ROOT / "data/research_receipts/h06_local_input_recovery_2026_09_14.json").read_text())
    assert receipt["integrity"]["receipt_sha256"] == h06.nested_self_hash(receipt, "receipt_sha256")
    assert receipt["integrity"]["receipt_sha256"] == canonical_hash(
        receipt, "receipt_sha256", container="integrity"
    )


def test_tracked_probe_inventory_matches_frozen_receipt() -> None:
    receipt = json.loads((ROOT / "data/research_manifests/h06_source_probe_receipt_2026_09_14.json").read_text())
    for source in receipt["sources"]:
        if source.get("persisted") is False:
            continue
        path = ROOT / source["path"]
        assert path.stat().st_size == source["bytes"]
        assert h06.sha256_file(path) == source["sha256"]


def test_required_input_hash_mismatch_blocks_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    relative = "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text("different")
    monkeypatch.setattr(h06, "REPO", tmp_path)
    monkeypatch.setattr(h06, "EXPECTED_V5", {relative: "0" * 64})
    source = tmp_path / "data/research_manifests/h06_source_probe_receipt_2026_09_14.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"sources": []}')
    with pytest.raises(ValueError, match="required_input_hash_or_bytes_mismatch"):
        h06.build_recovery_receipt()


def test_common_class_passes() -> None:
    assert h06.classify_security_class("Class A Common Stock, par value $0.01") == "COMMON_EQUITY_EXACT"


def test_community_does_not_match_unit_token() -> None:
    assert h06.classify_security_class("Community Financial Equity") == "AMBIGUOUS_SECURITY_CLASS"


def test_preferred_fails_common_gate() -> None:
    assert h06.classify_security_class("Series A Preferred Stock") == "PREFERRED_EQUITY"


def test_adr_fails_common_gate() -> None:
    assert h06.classify_security_class("American Depositary Shares") == "ADR_OR_ADS"


def test_warrant_fails_common_gate() -> None:
    assert h06.classify_security_class("Warrants to purchase common stock") == "WARRANT"


def _life(symbol: str, start: str, end: str = "", entity: str = "uuid") -> dict[str, str]:
    return {"canonical_symbol": symbol, "historical_symbol": symbol, "valid_from": start, "valid_to": end, "entity_id": entity}


def test_exact_v5_interval_join() -> None:
    result = h06.match_v5_interval("ABC", datetime(2024, 1, 2, tzinfo=UTC), [_life("ABC", "2020-01-01T00:00:00Z")])
    assert result["status"] == "V5_INTERVAL_EXACT"


def test_duplicate_interval_ambiguity() -> None:
    rows = [_life("ABC", "2020-01-01T00:00:00Z", entity="a"), _life("ABC", "2021-01-01T00:00:00Z", entity="b")]
    assert h06.match_v5_interval("ABC", datetime(2024, 1, 2, tzinfo=UTC), rows)["status"] == "IDENTITY_AMBIGUOUS"


def test_unresolved_ticker_reuse_blocks_exact_join() -> None:
    rows = [_life("ABC", "2020-01-01T00:00:00Z")]
    assert h06.match_v5_interval("ABC", datetime(2024, 1, 2, tzinfo=UTC), rows, unresolved_ticker_reuse=True)["status"] == "IDENTITY_AMBIGUOUS"


def test_v5_interval_requires_provider_uuid_and_aware_time() -> None:
    row = _life("ABC", "2020-01-01T00:00:00Z", entity="")
    assert h06.match_v5_interval("ABC", datetime(2024, 1, 2, tzinfo=UTC), [row])["status"] == "IDENTITY_MISSING"
    with pytest.raises(ValueError, match="interval_timestamp_timezone_required"):
        h06.match_v5_interval("ABC", datetime(2024, 1, 2), [_life("ABC", "2020-01-01T00:00:00Z")])


def test_ticker_change_chronology() -> None:
    old = {"issuer_cik": "1", "symbol": "OLD", "accepted_at": "2022-01-01T00:00:00Z"}
    new = {"issuer_cik": "1", "symbol": "NEW", "accepted_at": "2022-06-01T00:00:00Z"}
    rows = [_life("OLD", "2020-01-01T00:00:00Z", "2022-05-31T23:59:59Z"), _life("NEW", "2022-06-01T00:00:00Z")]
    assert h06.ticker_change_status(old, new, rows, class_evidence=True, form4_compatible=True) == "SAME_CIK_TICKER_CONTINUITY_EXACT"


def test_future_filing_cannot_rewrite_past() -> None:
    rows = [{"available_at": "2024-01-01T00:00:00Z", "symbol": "OLD"}, {"available_at": "2024-03-01T00:00:00Z", "symbol": "NEW"}]
    visible = h06.visible_states_as_of(rows, datetime(2024, 2, 1, tzinfo=UTC))
    assert [row["symbol"] for row in visible] == ["OLD"]


def _fact(available: str, instant: str, value: str, context: str = "c1", security_class: str | None = None, accepted: str = "2023-12-27T12:00:00Z") -> dict[str, object]:
    return {"issuer_cik": "1", "accession": "a", "tag": "dei:EntityCommonStockSharesOutstanding", "form": "10-Q", "accepted_at": accepted, "available_at": available, "fact_instant": instant, "value": value, "unit": "shares", "context_id": context, "dimensions": [], "security_class": security_class, "source_sha256": "f" * 64}


def test_multi_class_ambiguity() -> None:
    fact = _fact("2024-01-01T00:00:00Z", "2023-12-31", "100", security_class=None)
    assert h06.select_shares_fact([fact], datetime(2024, 2, 1, tzinfo=UTC), required_class="Class A")["status"] == "BLOCKED_SHARES_CLASS_AMBIGUOUS"


def test_future_shares_filing_excluded() -> None:
    fact = _fact("2024-03-05T14:30:00Z", "2023-12-31", "100", accepted="2024-03-01T12:00:00Z")
    assert h06.select_shares_fact([fact], datetime(2024, 2, 1, tzinfo=UTC))["status"] == "BLOCKED_SHARES_MISSING"


def test_future_fact_instant_excluded() -> None:
    fact = _fact("2024-01-01T00:00:00Z", "2024-03-01", "100")
    assert h06.select_shares_fact([fact], datetime(2024, 2, 1, tzinfo=UTC))["status"] == "BLOCKED_SHARES_MISSING"


def test_conflicting_shares_contexts_blocked() -> None:
    facts = [_fact("2024-01-01T00:00:00Z", "2023-12-31", "100", "a"), _fact("2024-01-01T00:00:00Z", "2023-12-31", "101", "b")]
    assert h06.select_shares_fact(facts, datetime(2024, 2, 1, tzinfo=UTC))["status"] == "BLOCKED_SHARES_CLASS_AMBIGUOUS"


def test_shares_fact_requires_dimensions_and_timing_v2() -> None:
    fact = _fact("2023-12-28T14:30:00Z", "2023-12-27", "100")
    assert h06.select_shares_fact([fact], datetime(2024, 2, 1, tzinfo=UTC))["status"] == "BLOCKED_SHARES_MISSING"
    exact = _fact("2024-01-02T14:30:00Z", "2023-12-31", "100")
    assert h06.select_shares_fact([exact], datetime(2024, 2, 1, tzinfo=UTC))["status"] == "SHARES_FACT_EXACT"


def test_split_ratio_orientation() -> None:
    assert h06.split_ratio({"old_rate": "1", "new_rate": "4"}) == Decimal(4)
    assert h06.split_ratio({"old_rate": "8", "new_rate": "1"}) == Decimal("0.125")


def test_forward_split_bridge() -> None:
    factor = h06.cumulative_split_factor([{"old_rate": "1", "new_rate": "10"}])
    assert h06.transform_shares(Decimal(100), factor) == Decimal(1000)


def test_reverse_split_bridge() -> None:
    factor = h06.cumulative_split_factor([{"old_rate": "8", "new_rate": "1"}])
    assert h06.transform_shares(Decimal(800), factor) == Decimal(100)


def test_no_split_factor_one() -> None:
    assert h06.cumulative_split_factor([]) == Decimal(1)


def test_market_cap_basis_invariant() -> None:
    assert h06.market_cap_basis_invariant(Decimal(100), Decimal(10), Decimal(25), Decimal(40))


def test_h06_timing_v2_complete_business_day() -> None:
    accepted = datetime.fromisoformat("2024-05-06T12:00:00-04:00")  # Monday
    assert h06.h06_timing_v2(accepted).astimezone(h06.NY).isoformat().startswith("2024-05-08T09:30:00")


def test_h06_timing_v2_holiday_weekend() -> None:
    accepted = datetime.fromisoformat("2024-07-03T12:00:00-04:00")  # Wed before Independence Day
    assert h06.h06_timing_v2(accepted).astimezone(h06.NY).isoformat().startswith("2024-07-08T09:30:00")


def test_h06_timing_v2_rejects_naive_acceptance() -> None:
    with pytest.raises(ValueError, match="acceptance_timezone_required"):
        h06.h06_timing_v2(datetime(2024, 1, 2, 12, 0))


def test_identity_exact_requires_every_predicate() -> None:
    complete = {"issuer_cik_exact": True, "accepted_security_state": True, "form4_symbol_agrees": True, "common_equity": True, "transaction_title_compatible": True, "v5_interval_status": "V5_INTERVAL_EXACT", "ticker_reuse_resolved": True}
    assert h06.resolve_identity(**complete) == "IDENTITY_EXACT"
    assert h06.resolve_identity(**(complete | {"form4_symbol_agrees": False})) == "IDENTITY_AMBIGUOUS"


def test_no_economic_fields_in_probe_rows() -> None:
    probe = json.loads((ROOT / "data/research_receipts/h06_identity_marketcap_probe_2026_09_14.json").read_text())
    assert not h06.contains_economic_output({"identity_probe": probe["identity_probe"], "market_cap_probe": probe["market_cap_probe"]})


def test_no_h06_economic_runner() -> None:
    source = SCRIPT.read_text().casefold()
    assert "run_equity_local_diagnostics" not in source
    assert "backtest" not in source


def test_no_retrospective_outcome_artifact_access() -> None:
    source = SCRIPT.read_text()
    assert "equity_local_diagnostics_2026_09_09" not in source
    assert "research_runs/equity_h06" not in source


def test_protocol_and_basis_fail_closed() -> None:
    manifest = json.loads((ROOT / "data/research_manifests/h06_identity_marketcap_bridge_2026_09_14.json").read_text())
    assert manifest["shares"]["protocol_compatibility"] == "LATEST_ACCEPTED_REPORTED_SHARES_WOULD_BE_A_PROTOCOL_CHANGE"
    assert manifest["price_basis"]["status"] == "V5_SPLIT_BASIS_NOT_PROVEN"
    assert manifest["status"] == "H06_IDENTITY_MARKETCAP_BLOCKED"


def test_canonical_hash_does_not_mutate() -> None:
    payload = {"integrity": {"receipt_sha256": ""}, "value": 1}
    before = json.loads(json.dumps(payload))
    h06.nested_self_hash(payload, "receipt_sha256")
    assert payload == before
