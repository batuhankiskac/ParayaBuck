from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from parayabuck.backtest.lifetimes import (
    CANONICAL_LIFETIME_FIELDS,
    LifetimeSchemaError,
    load_entity_lifetimes,
)


def _row(**changes: str) -> dict[str, str]:
    row = {
        "entity_id": "11111111-1111-4111-8111-111111111111",
        "provider_asset_id": "21111111-1111-4111-8111-111111111111",
        "canonical_symbol": "AAA",
        "historical_symbol": "AAA",
        "valid_from": "2020-01-02T05:00:00+00:00",
        "valid_to": "",
        "first_observed_bar": "2020-01-02T05:00:00+00:00",
        "last_observed_bar": "2026-07-30T04:00:00+00:00",
        "current_status": "active",
        "exchange": "NYSE",
        "rename_chain": "",
        "evidence_class": "FINAL_SURVIVOR",
        "pit_quality_tier": "TIER_A_VERIFIED_PIT",
        "terminal_event_class": "",
        "notes": "fixture",
    }
    row.update(changes)
    return row


def _write(path: Path, rows: list[dict[str, str]], fields: tuple[str, ...] = CANONICAL_LIFETIME_FIELDS) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_loader_classifies_terminals_horizon_and_unresolved_without_pre_exclusion(tmp_path: Path) -> None:
    terminal = _row(
        entity_id="22222222-2222-4222-8222-222222222222",
        provider_asset_id="32222222-2222-4222-8222-222222222222",
        canonical_symbol="OLD",
        historical_symbol="OLD",
        valid_to="2022-06-30T04:00:00Z",
        last_observed_bar="2022-06-30T04:00:00Z",
        current_status="inactive",
        evidence_class="POSSIBLE_DELISTING",
        terminal_event_class="POSSIBLE_DELISTING",
    )
    unresolved = _row(
        entity_id="33333333-3333-4333-8333-333333333333",
        provider_asset_id="43333333-3333-4333-8333-333333333333",
        canonical_symbol="EOSEW",
        historical_symbol="EOSEW",
        current_status="inactive",
    )
    result = load_entity_lifetimes(_write(tmp_path / "lifetimes.csv", [_row(), terminal, unresolved]))

    assert len(result.records) == 3
    assert result.horizon_censored == ("AAA",)
    assert result.unresolved[0].canonical_symbol == "EOSEW"
    assert result.unresolved[0].reasons == (
        "inactive_entity_missing_valid_to",
        "inactive_entity_missing_terminal_event",
    )
    mapping = result.engine_terminal_lifecycles()
    assert set(mapping) == {"OLD"}
    assert mapping["OLD"].last_bar.isoformat() == "2022-06-30T04:00:00+00:00"
    assert mapping["OLD"].event_class == "POSSIBLE_DELISTING"
    assert result.receipt.to_dict()["explicit_terminal_count"] == 1
    assert result.receipt.horizon_censored_count == 1
    assert result.receipt.unresolved_count == 1


def test_loader_validates_and_types_rename_chain(tmp_path: Path) -> None:
    chain = json.dumps(
        [
            {"symbol": "FB", "valid_to": "2022-06-08"},
            {"symbol": "META", "valid_from": "2022-06-09"},
        ],
        separators=(",", ":"),
    )
    result = load_entity_lifetimes(
        _write(
            tmp_path / "renamed.csv",
            [
                _row(
                    canonical_symbol="META",
                    historical_symbol="META",
                    rename_chain=chain,
                )
            ],
        )
    )

    intervals = result.records[0].rename_chain
    assert [interval.symbol for interval in intervals] == ["FB", "META"]
    assert intervals[0].to_dict() == {"symbol": "FB", "valid_from": None, "valid_to": "2022-06-08"}
    assert intervals[1].to_dict() == {"symbol": "META", "valid_from": "2022-06-09", "valid_to": None}
    assert result.receipt.rename_chain_count == 1


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"entity_id": "not-a-uuid"}, "invalid entity_id"),
        ({"valid_from": "2020-01-02"}, "timezone-aware"),
        ({"last_observed_bar": "2019-12-31T05:00:00+00:00"}, "invalid valid_from/observed-bar interval"),
        ({"current_status": "unknown"}, "unsupported current_status"),
        ({"canonical_symbol": "aaa"}, "non-canonical canonical_symbol"),
    ],
)
def test_loader_rejects_invalid_ids_timestamps_intervals_status_and_symbols(
    tmp_path: Path,
    changes: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(LifetimeSchemaError, match=message):
        load_entity_lifetimes(_write(tmp_path / "invalid.csv", [_row(**changes)]))


def test_loader_rejects_missing_or_extra_schema_columns(tmp_path: Path) -> None:
    missing = tuple(field for field in CANONICAL_LIFETIME_FIELDS if field != "notes")
    with pytest.raises(LifetimeSchemaError, match="Invalid lifetime CSV schema"):
        load_entity_lifetimes(_write(tmp_path / "missing.csv", [_row()], missing))

    extra = (*CANONICAL_LIFETIME_FIELDS, "unexpected")
    with pytest.raises(LifetimeSchemaError, match="Invalid lifetime CSV schema"):
        load_entity_lifetimes(_write(tmp_path / "extra.csv", [{**_row(), "unexpected": "x"}], extra))


@pytest.mark.parametrize("duplicate_field", ["entity_id", "provider_asset_id", "canonical_symbol"])
def test_loader_rejects_duplicate_identity_keys(tmp_path: Path, duplicate_field: str) -> None:
    second = _row(
        entity_id="22222222-2222-4222-8222-222222222222",
        provider_asset_id="32222222-2222-4222-8222-222222222222",
        canonical_symbol="BBB",
        historical_symbol="BBB",
    )
    second[duplicate_field] = _row()[duplicate_field]
    with pytest.raises(LifetimeSchemaError, match=f"duplicate {duplicate_field}"):
        load_entity_lifetimes(_write(tmp_path / "duplicate.csv", [_row(), second]))


@pytest.mark.parametrize(
    ("chain", "message"),
    [
        ("not json", "invalid rename_chain JSON"),
        (
            json.dumps([{"symbol": "FB", "valid_to": "2022-06-10"}, {"symbol": "META", "valid_from": "2022-06-09"}]),
            "overlapping or unordered",
        ),
        (
            json.dumps([{"symbol": "FB", "valid_to": "2022-06-08"}, {"symbol": "MMETA", "valid_from": "2022-06-09"}]),
            "must end at canonical_symbol",
        ),
        (json.dumps([{"symbol": "META", "unknown": "x"}]), "invalid rename_chain entry"),
    ],
)
def test_loader_rejects_invalid_rename_json(tmp_path: Path, chain: str, message: str) -> None:
    with pytest.raises(LifetimeSchemaError, match=message):
        load_entity_lifetimes(
            _write(
                tmp_path / "invalid-rename.csv",
                [_row(canonical_symbol="META", historical_symbol="META", rename_chain=chain)],
            )
        )


def test_terminal_boundary_mismatch_is_unresolved_and_not_emitted(tmp_path: Path) -> None:
    result = load_entity_lifetimes(
        _write(
            tmp_path / "mismatch.csv",
            [
                _row(
                    valid_to="2026-07-31T04:00:00+00:00",
                    current_status="inactive",
                    terminal_event_class="POSSIBLE_DELISTING",
                )
            ],
        )
    )

    assert result.engine_terminal_lifecycles() == {}
    assert result.unresolved[0].reasons == ("terminal_boundary_does_not_match_last_observed_bar",)


def test_receipt_hashes_are_deterministic_and_sensitive_to_terminal_mapping(tmp_path: Path) -> None:
    first_path = _write(
        tmp_path / "first.csv",
        [
            _row(
                valid_to="2026-07-30T04:00:00+00:00",
                current_status="inactive",
                terminal_event_class="POSSIBLE_DELISTING",
            )
        ],
    )
    first = load_entity_lifetimes(first_path)
    again = load_entity_lifetimes(first_path)
    assert first.receipt.source_sha256 == again.receipt.source_sha256
    assert first.receipt.terminal_mapping_sha256 == again.receipt.terminal_mapping_sha256

    changed = load_entity_lifetimes(
        _write(
            tmp_path / "changed.csv",
            [
                _row(
                    valid_to="2026-07-30T04:00:00+00:00",
                    current_status="inactive",
                    terminal_event_class="MERGER",
                )
            ],
        )
    )
    assert first.receipt.terminal_mapping_sha256 != changed.receipt.terminal_mapping_sha256
