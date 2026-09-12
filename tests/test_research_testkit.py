from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import Market
from parayabuck.data.market_dataset import CorporateActionType
from tests.research_testkit import (
    bar,
    bar_series,
    canonical_hash,
    corporate_action,
    pit_universe,
    sha256_file,
    snapshot,
    terminal_info,
    write_artifact,
)

START = datetime(2025, 1, 2, tzinfo=UTC)


def test_bar_and_series_are_deterministic_and_immutable() -> None:
    first = bar_series("AAA", [100.0, 101.0], start=START)
    second = bar_series("AAA", [100.0, 101.0], start=START)

    assert first == second
    assert isinstance(first, tuple)
    assert first[0].timestamp == START
    assert first[1].timestamp == START + timedelta(days=1)
    assert first[0].high == first[0].low == first[0].close == 100.0


def test_bar_preserves_explicit_market_and_ohlc_values() -> None:
    value = bar(
        START,
        "BTC/USD",
        99.0,
        market=Market.CRYPTO,
        timeframe="1h",
        open=100.0,
        high=102.0,
        low=98.0,
        volume=12.0,
    )

    assert (value.symbol, value.market, value.timeframe) == ("BTC/USD", Market.CRYPTO, "1h")
    assert (value.open, value.high, value.low, value.close, value.volume) == (100.0, 102.0, 98.0, 99.0, 12.0)


def test_pit_fixture_retains_observation_order_and_rejects_implicit_sorting() -> None:
    first = snapshot(START, ("SPY", "AAA"), observation_set=("SPY", "AAA"), pinned_symbols=("SPY",))
    second = snapshot(START + timedelta(days=1), ("AAA", "SPY"), observation_set=("AAA", "SPY"))
    universe = pit_universe(Market.EQUITY, (first, second))

    assert universe.snapshots == (first, second)
    assert universe.snapshots[0].observation_set == ("SPY", "AAA")
    with pytest.raises(ValueError, match="ascending"):
        pit_universe(Market.EQUITY, (second, first))


def test_event_factories_keep_domain_validation() -> None:
    action = corporate_action(START, "AAA", CorporateActionType.DIVIDEND, 1.25)
    terminal = terminal_info(
        entity_id="entity-aaa",
        symbol="AAA",
        last_bar=START,
        terminal_event_class="POSSIBLE_DELISTING",
        evidence_class="B",
        should_haircut=True,
    )

    assert action.action_type is CorporateActionType.DIVIDEND
    assert terminal.display_symbol == "AAA"
    with pytest.raises(ValueError, match="positive"):
        corporate_action(START, "AAA", CorporateActionType.SPLIT, 0.0)


def test_artifact_writer_is_root_bound_and_non_overwriting(tmp_path) -> None:
    path = write_artifact(tmp_path, "nested/receipt.json", "{}\n")
    assert path.read_text(encoding="utf-8") == "{}\n"
    assert sha256_file(path)
    with pytest.raises(FileExistsError):
        write_artifact(tmp_path, "nested/receipt.json", "changed\n")
    with pytest.raises(ValueError):
        write_artifact(tmp_path, "../outside.json", "blocked")


def test_canonical_hash_requires_explicit_self_field_and_does_not_mutate_input() -> None:
    payload = {"integrity": {"receipt_sha256": "old"}, "status": "COMPLETE", "text": "é"}
    original = {"integrity": dict(payload["integrity"]), "status": "COMPLETE", "text": "é"}
    digest = canonical_hash(payload, "receipt_sha256", container="integrity")

    assert payload == original
    assert digest == canonical_hash(payload, "receipt_sha256", container="integrity")
    with pytest.raises(ValueError, match="integrity object"):
        canonical_hash({"status": "COMPLETE"}, "receipt_sha256", container="integrity")
