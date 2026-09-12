from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.research.verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10 import verify_attestation

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10.json"
ATTESTATION = REPO / "data/research_receipts/r46_treasury_auction_unwind_tlt_v2_2026_09_10_native_attestation.json"
EXECUTION_TEST = "tests/research/test_r46_v2_execution_2026_09_10.py"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_r46_frozen_source_pins_match_native_attestation_and_governance() -> None:
    attestation = _read_json(ATTESTATION)
    manifest = _read_json(MANIFEST)

    assert verify_attestation(attestation, repo_root=REPO)["status"] == "VERIFIED_NATIVE_LEDGER"

    relative_manifest = MANIFEST.relative_to(REPO).as_posix()
    assert attestation["governance_manifest_path"] == relative_manifest
    assert attestation["governance_canonical_sha256"] == manifest["integrity"]["manifest_sha256"]
    assert attestation["manifest_sha256"] == manifest["integrity"]["manifest_sha256"]

    verifier_path = REPO / attestation["verifier_path"]
    assert verifier_path.is_file()
    verifier_sha = _sha256(verifier_path)
    assert verifier_sha == attestation["verifier_sha256"]

    manifest_verifier = manifest["source_provenance"]["verifier"]
    assert manifest_verifier["path"] == attestation["verifier_path"]
    assert manifest_verifier["sha256"] == attestation["verifier_sha256"]
    assert verifier_sha == manifest_verifier["sha256"]

    execution_pin = manifest["source_provenance"]["source_files"][EXECUTION_TEST]
    execution_path = REPO / execution_pin["path"]
    assert execution_path.is_file()
    assert execution_pin["path"] == EXECUTION_TEST
    assert _sha256(execution_path) == execution_pin["sha256"]
