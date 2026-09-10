from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.research import build_equity_h01_h16_protocol_2026_09_09 as protocol


def test_definitions_freeze_all_candidates_and_shared_contract() -> None:
    cards = protocol.definitions()
    assert list(cards) == protocol.EXPECTED_IDS
    for candidate_id, card in cards.items():
        assert card["candidate_id"] == candidate_id
        for field in ("mechanism", "formula", "direction", "rebalance", "holding", "missing_data", "security_continuity", "blocker_if_unavailable"):
            assert isinstance(card[field], str) and card[field]
        assert card["costs"] == {"multipliers": [1, 2, 3], "base": "frozen current equity execution-cost model", "fitting": False}
        assert set(card["windows"]) == {"full", "recent", "true_holdout"}
        assert card["overlay"]["equity_cap"] == 0.55
        assert card["overlay"]["gross_cap"] == 0.60
        assert card["overlay"]["cash_floor"] == 0.40
        assert card["overlay"]["positions_cap"] == 6


def test_protocol_freezes_h14_and_separate_h14b_variants() -> None:
    payload = protocol.protocol_payload()
    assert payload["candidate_ids"] == protocol.EXPECTED_IDS
    assert payload["h14b"]["status"] == "FROZEN_DIAGNOSTIC_VARIANTS_NOT_PART_OF_H14_RESULT"
    assert [variant["id"] for variant in payload["h14b"]["variants"]] == [
        "H14b_spare_slot_only",
        "H14b_lowest_ranked_slot_substitution",
    ]
    assert all(variant["status"].startswith("NOT_RUN_") for variant in payload["h14b"]["variants"])
    assert "no OHLCV substitution" in " ".join(payload["research_boundaries"])
    assert payload["common"]["windows"]["true_holdout"]["start"] == "2026-01-01T00:00:00Z"


def test_build_verify_and_deterministic_rebuild() -> None:
    first = protocol.build()
    first_bytes = {path: Path(path).read_bytes() for path in (first["protocol"], first["results"], first["manifest"], first["receipt"])}
    second = protocol.build()
    second_bytes = {path: Path(path).read_bytes() for path in (second["protocol"], second["results"], second["manifest"], second["receipt"])}
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["receipt_sha256"] == second["receipt_sha256"]
    assert first_bytes == second_bytes
    assert protocol.verify()["status"] == "FROZEN_PROTOCOL_VERIFIED"


@pytest.mark.parametrize("target", [protocol.MANIFEST, protocol.RESULTS, protocol.RECEIPT])
def test_mutated_current_artifact_fails_closed(target: Path) -> None:
    original = target.read_bytes()
    try:
        if target == protocol.MANIFEST:
            payload = json.loads(original)
            payload["candidate_count"] = 15
            target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        elif target == protocol.RESULTS:
            payload = json.loads(original)
            payload["rows"][0]["integration_candidate"] = True
            target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            payload = json.loads(original)
            payload["receipt_sha256"] = "0" * 64
            target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with pytest.raises(ValueError):
            protocol.verify()
    finally:
        target.write_bytes(original)


def test_manifest_and_receipt_bind_canonical_hashes() -> None:
    manifest = json.loads(protocol.MANIFEST.read_text(encoding="utf-8"))
    receipt = json.loads(protocol.RECEIPT.read_text(encoding="utf-8"))
    assert manifest["manifest_sha256"] == protocol.canonical_hash(manifest, "manifest_sha256")
    assert receipt["receipt_sha256"] == protocol.canonical_hash(receipt, "receipt_sha256")
    assert receipt["manifest"]["canonical_sha256"] == manifest["manifest_sha256"]
    assert receipt["manifest"]["sha256"] == hashlib.sha256(protocol.MANIFEST.read_bytes()).hexdigest()
