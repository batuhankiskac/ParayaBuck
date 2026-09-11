from __future__ import annotations

import hashlib
import inspect
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
FINAL_RUN_RELATIVE = Path(
    "data/research/runs/r46_treasury_xml_v2_2026_09_11_inventory_raw_hash_2026_09_11"
)
PUBLIC_RECEIPT_RELATIVE = Path("data/research_receipts/r46_treasury_public_source_evidence_2026_09_10_v1.json")
EVIDENCE_RELATIVE = Path(
    "data/research_receipts/r46_treasury_publication_availability_evidence_2026_09_11_inventory_raw_hash_v3.json"
)
POLICY = "official_XML_AuctionDate_plus_one_civil_day_midnight_America_New_York_for_all_results"
ELIGIBILITY = "FIRST_REGULAR_OPEN_STRICTLY_AFTER_AVAILABLE_AT"
MISSING_IDS = [
    "2016-01-14:912810RP5",
    "2016-02-11:912810RQ3",
    "2016-03-10:912810RQ3",
    "2016-04-14:912810RQ3",
    "2016-05-12:912810RS9",
    "2016-06-09:912810RS9",
    "2016-07-13:912810RS9",
    "2016-08-11:912810RT7",
    "2016-09-13:912810RT7",
    "2016-10-13:912810RT7",
    "2016-11-10:912810RU4",
    "2016-12-13:912810RU4",
    "2017-01-12:912810RU4",
    "2017-02-09:912810RV2",
    "2017-03-09:912810RV2",
]


def _canonical_receipt_hash(payload: dict[str, Any]) -> str:
    value = json.loads(json.dumps(payload, ensure_ascii=False))
    value["integrity"].pop("receipt_sha256", None)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _copy_final_inputs(tmp_path: Path) -> Path:
    run_target = tmp_path / FINAL_RUN_RELATIVE
    shutil.copytree(REPO / FINAL_RUN_RELATIVE, run_target)
    public_target = tmp_path / PUBLIC_RECEIPT_RELATIVE
    public_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO / PUBLIC_RECEIPT_RELATIVE, public_target)
    receipt_path = run_target / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["inventory"]["path"] = PUBLIC_RECEIPT_RELATIVE.as_posix()
    receipt["integrity"]["receipt_sha256"] = _canonical_receipt_hash(receipt)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return tmp_path


def test_evidence_builder_derives_exact_policy_and_sources_from_final_receipt(tmp_path: Path) -> None:
    root = _copy_final_inputs(tmp_path)
    from scripts.research.build_r46_publication_availability_evidence_2026_09_11 import (
        build_publication_availability_evidence,
    )

    output = root / EVIDENCE_RELATIVE
    evidence = build_publication_availability_evidence(root, output_path=output)

    assert evidence["schema"] == "r46-treasury-publication-availability-evidence-v2"
    assert evidence["status"] == "COMPLETE"
    assert evidence["research_id"] == "r46_treasury_auction_unwind_tlt_v2_2026_09_10"
    assert evidence["candidate_id"] == "R46"
    assert evidence["result_availability_policy"] == POLICY
    assert evidence["result_signal_eligibility"] == ELIGIBILITY
    assert evidence["same_day_signal_allowed"] is False
    assert evidence["actual_release_time_role"] == "evidence_only"
    assert evidence["server_last_modified_is_publication_proof"] is False
    assert evidence["expected_event_count"] == 127
    assert evidence["expected_event_ids"] == sorted(evidence["expected_event_ids"])
    assert len(evidence["expected_event_ids"]) == 127
    assert evidence["missing_release"]["expected_count"] == 15
    assert evidence["missing_release"]["expected_event_ids"] == MISSING_IDS
    assert evidence["missing_release"]["expected_event_ids_sha256"] == evidence["missing_release"]["expected_event_ids_digest"]
    assert evidence["sources"]["receipt"]["path"] == (FINAL_RUN_RELATIVE / "receipt.json").as_posix()
    assert evidence["sources"]["documents"]["path"] == (FINAL_RUN_RELATIVE / "documents.jsonl").as_posix()
    assert evidence["integrity"]["evidence_sha256"]
    assert output.is_file()


def test_evidence_builder_rejects_alternate_missing_ids_after_receipt_rehash(tmp_path: Path) -> None:
    root = _copy_final_inputs(tmp_path)
    receipt_path = root / FINAL_RUN_RELATIVE / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["publication_evidence"]["result_release_time_missing_event_ids"] = MISSING_IDS[:-1] + [
        "2017-03-09:CALLER"
    ]
    receipt["integrity"]["receipt_sha256"] = _canonical_receipt_hash(receipt)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    from scripts.research.build_r46_publication_availability_evidence_2026_09_11 import (
        PublicationAvailabilityEvidenceError,
        build_publication_availability_evidence,
    )

    with pytest.raises(PublicationAvailabilityEvidenceError, match="missing release"):
        build_publication_availability_evidence(root, output_path=root / EVIDENCE_RELATIVE)


def test_evidence_builder_rejects_last_modified_and_same_day_policy_spoof(tmp_path: Path) -> None:
    root = _copy_final_inputs(tmp_path)
    receipt_path = root / FINAL_RUN_RELATIVE / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["publication_evidence"]["same_day_result_signals_allowed"] = True
    receipt["publication_evidence"]["server_last_modified_is_publication_proof"] = True
    receipt["integrity"]["receipt_sha256"] = _canonical_receipt_hash(receipt)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    from scripts.research.build_r46_publication_availability_evidence_2026_09_11 import (
        PublicationAvailabilityEvidenceError,
        build_publication_availability_evidence,
    )

    with pytest.raises(PublicationAvailabilityEvidenceError, match="publication policy"):
        build_publication_availability_evidence(root, output_path=root / EVIDENCE_RELATIVE)


def test_evidence_builder_rejects_receipt_documents_hash_spoof(tmp_path: Path) -> None:
    root = _copy_final_inputs(tmp_path)
    receipt_path = root / FINAL_RUN_RELATIVE / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["documents_sha256"] = "0" * 64
    receipt["integrity"]["receipt_sha256"] = _canonical_receipt_hash(receipt)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    from scripts.research.build_r46_publication_availability_evidence_2026_09_11 import (
        PublicationAvailabilityEvidenceError,
        build_publication_availability_evidence,
    )

    with pytest.raises(PublicationAvailabilityEvidenceError, match="documents hash"):
        build_publication_availability_evidence(root, output_path=root / EVIDENCE_RELATIVE)


def test_evidence_builder_rejects_receipt_self_hash_tamper(tmp_path: Path) -> None:
    root = _copy_final_inputs(tmp_path)
    receipt_path = root / FINAL_RUN_RELATIVE / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["integrity"]["receipt_sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    from scripts.research.build_r46_publication_availability_evidence_2026_09_11 import (
        PublicationAvailabilityEvidenceError,
        build_publication_availability_evidence,
    )

    with pytest.raises(PublicationAvailabilityEvidenceError, match="self-hash"):
        build_publication_availability_evidence(root, output_path=root / EVIDENCE_RELATIVE)


def test_evidence_builder_has_no_caller_event_universe_parameter() -> None:
    from scripts.research.build_r46_publication_availability_evidence_2026_09_11 import (
        build_publication_availability_evidence,
    )

    assert "expected_event_ids" not in inspect.signature(build_publication_availability_evidence).parameters
