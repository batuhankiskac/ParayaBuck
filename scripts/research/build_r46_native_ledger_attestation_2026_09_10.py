#!/usr/bin/env python3
"""Build the R46 v2 native structural receipt.

The builder performs only a deterministic synthetic smoke: the bars are
synthetic, but the actual ``PortfolioBacktestEngine`` and explicit dividend
path are exercised.  It never loads the R46 data package or emits economic
outcomes.  A receipt is READY only when the native smoke and independent
verifier succeed and every v2 source/package pin is supplied.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.research.r46_treasury_auction_unwind_tlt_v2_2026_09_10 import (
    RESEARCH_ID,
    native_adapter_smoke,
)
from scripts.research.verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10 import verify_attestation

RUNNER = "scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py"
VERIFIER = "scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py"
SOURCE = "src/parayabuck/backtest/portfolio_engine.py"
SCHEMA = "r46-native-ledger-attestation-v2"
DEFAULT_GOVERNANCE = REPO / "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10.json"
DEFAULT_PACKAGE = REPO / "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_data_package.json"


def _canonical_hash(payload: dict[str, Any], field: str) -> str:
    body = copy.deepcopy(payload)
    body.pop(field, None)
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_artifact_hash(path: Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("artifact_json_object_required")
    integrity = value.get("integrity")
    if not isinstance(integrity, dict) or not isinstance(integrity.get("manifest_sha256"), str):
        raise ValueError("artifact_canonical_hash_missing")
    body = copy.deepcopy(value)
    body["integrity"].pop("manifest_sha256")
    computed = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if value["integrity"]["manifest_sha256"] != computed:
        raise ValueError("artifact_canonical_hash_invalid")
    return computed


def _path_or_none(value: Path | str | None, default: Path) -> Path | None:
    path = Path(value) if value is not None else default
    return path if path.is_file() else None


def _resolve_hash(value: str | None, path: Path | None, *, label: str) -> str | None:
    if path is None:
        if value is not None:
            raise ValueError(f"ATTESTATION_PIN_MISSING:{label}")
        return None
    try:
        actual = _canonical_artifact_hash(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        if value is not None:
            raise ValueError(f"ATTESTATION_PIN_MISSING:{label}")
        return None
    if value is not None and value != actual:
        raise ValueError(f"ATTESTATION_PIN_MISMATCH:{label}")
    return actual


def _pin_or_none(value: str | None, path: Path, *, label: str) -> str | None:
    try:
        actual = _sha256_file(path)
    except OSError:
        if value is not None:
            raise ValueError(f"ATTESTATION_PIN_MISSING:{label}")
        return None
    if value is not None and value != actual:
        raise ValueError(f"ATTESTATION_PIN_MISMATCH:{label}")
    return actual


def _source_file_path(relative: str) -> Path:
    candidate = Path(relative)
    path = candidate if candidate.is_absolute() else REPO / candidate
    try:
        path.resolve().relative_to(REPO.resolve())
    except ValueError as exc:
        raise ValueError("ATTESTATION_SOURCE_PATH_OUTSIDE_REPO") from exc
    return path


def build_attestation(
    *,
    governance_manifest_path: Path | str | None = None,
    data_package_manifest_path: Path | str | None = None,
    governance_canonical_sha256: str | None = None,
    package_canonical_sha256: str | None = None,
    runner_sha256: str | None = None,
    verifier_sha256: str | None = None,
    source_sha256: str | None = None,
    source_hashes: dict[str, str] | None = None,
) -> dict[str, object]:
    """Build a v2 receipt; missing pins leave it fail-closed BLOCKED."""

    governance_path = _path_or_none(governance_manifest_path, DEFAULT_GOVERNANCE)
    package_path = _path_or_none(data_package_manifest_path, DEFAULT_PACKAGE)
    governance_hash = _resolve_hash(governance_canonical_sha256, governance_path, label="governance")
    package_hash = _resolve_hash(package_canonical_sha256, package_path, label="package")
    runner_path = _source_file_path(RUNNER)
    verifier_path = _source_file_path(VERIFIER)
    source_path = _source_file_path(SOURCE)
    runner_hash = _pin_or_none(runner_sha256, runner_path, label="runner")
    verifier_hash = _pin_or_none(verifier_sha256, verifier_path, label="verifier")
    source_hash = _pin_or_none(source_sha256, source_path, label="source")
    validated_source_hashes: dict[str, str] = {}
    for relative, supplied in (source_hashes or {}).items():
        if not isinstance(relative, str) or not isinstance(supplied, str):
            raise ValueError("ATTESTATION_SOURCE_PIN_INVALID")
        validated_source_hashes[relative] = _pin_or_none(
            supplied,
            _source_file_path(relative),
            label=f"source:{relative}",
        ) or ""
    if source_hash is not None:
        validated_source_hashes.setdefault(SOURCE, source_hash)
    smoke = native_adapter_smoke()
    ledger = smoke.get("ledger") if isinstance(smoke.get("ledger"), dict) else None
    blockers: list[str] = []
    if smoke.get("status") != "READY_NATIVE_LEDGER" or ledger is None:
        blockers.append("BLOCKED_NATIVE_ADAPTER_SMOKE")
    for label, value in (
        ("governance", governance_hash),
        ("package", package_hash),
        ("runner", runner_hash),
        ("verifier", verifier_hash),
        ("source", source_hash),
    ):
        if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            blockers.append(f"BLOCKED_{label.upper()}_PIN")

    attestation: dict[str, object] = {
        "schema_version": SCHEMA,
        "status": "READY_NATIVE_LEDGER" if not blockers else "BLOCKED_NATIVE_LEDGER",
        "blockers": sorted(set(blockers)),
        "research_id": RESEARCH_ID,
        "candidate_id": "R46",
        "native_adapter_verified": not blockers,
        "native_engine": "PortfolioBacktestEngine",
        "engine_invocations": 1 if ledger is not None else 0,
        "synthetic_prices": True,
        "native_one_ledger": True,
        "shared_account": True,
        "economic_execution": False,
        "economic_run": False,
        "outcome_artifacts_present": False,
        "research_only": True,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "promotion": False,
        "integration_candidate": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
        "separate_sleeve_blend": False,
        "governance_canonical_sha256": governance_hash,
        "manifest_sha256": governance_hash,
        "package_canonical_sha256": package_hash,
        "package_sha256": package_hash,
        "runner_path": str(runner_path.relative_to(REPO)),
        "verifier_path": str(verifier_path.relative_to(REPO)),
        "source_path": str(source_path.relative_to(REPO)),
        "runner_sha256": runner_hash,
        "verifier_sha256": verifier_hash,
        "source_sha256": source_hash,
        "source_hashes": validated_source_hashes,
        "governance_manifest_path": str(governance_path.relative_to(REPO)) if governance_path and governance_path.is_relative_to(REPO) else None,
        "data_package_manifest_path": str(package_path.relative_to(REPO)) if package_path and package_path.is_relative_to(REPO) else None,
        "required_fields": [
            "decision_timestamp",
            "signal_asof_timestamp",
            "target_weights",
            "realized_holdings",
            "fills",
            "fees",
            "cash",
            "suppression_reason",
            "ownership_attribution",
        ],
        "fills": copy.deepcopy(ledger.get("fills", [])) if ledger is not None else [],
        "dividend_events": copy.deepcopy(ledger.get("dividend_events", [])) if ledger is not None else [],
        "ledger": copy.deepcopy(ledger) if ledger is not None else {},
        "attestation_sha256": "",
    }
    attestation["attestation_sha256"] = _canonical_hash(attestation, "attestation_sha256")
    if not blockers:
        try:
            verify_attestation(attestation, repo_root=REPO)
        except ValueError as exc:
            attestation["status"] = "BLOCKED_NATIVE_LEDGER"
            attestation["native_adapter_verified"] = False
            attestation["blockers"] = [f"BLOCKED_VERIFIER:{exc}"]
            attestation["attestation_sha256"] = _canonical_hash(attestation, "attestation_sha256")
    return attestation


def write_attestation(
    path: Path,
    *,
    payload: dict[str, object] | None = None,
    **kwargs: object,
) -> dict[str, object]:
    """Write once with O_EXCL; never overwrite a prior receipt."""

    value = payload if payload is not None else build_attestation(**kwargs)  # type: ignore[arg-type]
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    descriptor = path.open("xb")
    with descriptor:
        descriptor.write(encoded)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = write_attestation(args.output)
        print(json.dumps({"status": payload["status"], "attestation_sha256": payload["attestation_sha256"]}, sort_keys=True))
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_attestation", "write_attestation"]
