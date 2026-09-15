#!/usr/bin/env python3
"""Create the deterministic H06 one-shot authorization receipt."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
STRUCTURAL = REPO / "data/research_manifests/h06_full_structural_admission_2026_09_14.json"
PROTOCOL = REPO / "data/research_manifests/h06_final_economic_protocol_2026_09_15.json"
STATE = REPO / "data/research_manifests/h06_final_one_shot_state_2026_09_15.json"
RUNNER = REPO / "scripts/research/h06_final_economic_2026_09_15.py"
VERIFIER = REPO / "scripts/research/verify_h06_final_economic_2026_09_15.py"
OUTPUT = REPO / "data/research_receipts/h06_final_economic_authorization_2026_09_15.json"
BASE = "b9a136cb9d5bce725ba1e7b416653794c30be3c6"
C_ENRICH = "890f79281188ca225acc04220d249cbf90ed1341"
INPUTS = {
    "data/historical/equity_pit_symmetric_2016_2026_v5.csv": "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139",
    "data/historical/equity_entity_lifetimes_full_v5.csv": "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596",
    "data/historical/equity_survivorship_full_corporate_actions_v5.csv": "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23",
}
V3_SHA = "2c8b30eca960750a5bf31843d0577c087c835b6d822fa6a09cb6b61e161eab32"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def seal(value: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(value)
    payload.setdefault("integrity", {}).pop("self_sha256", None)
    value.setdefault("integrity", {})["self_sha256"] = hashlib.sha256(canonical(payload)).hexdigest()
    return value


def build() -> None:
    freeze = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True).stdout.strip()
    hashes = {"source_commit": BASE, "structural_commit": C_ENRICH, "freeze_commit": freeze,
              "structural_manifest_sha256": sha256(STRUCTURAL), "protocol_sha256": sha256(PROTOCOL),
              "runner_sha256": sha256(RUNNER), "verifier_sha256": sha256(VERIFIER),
              "one_shot_state_sha256": sha256(STATE), "canonical_outcome_input_hashes": INPUTS,
              "v3_reference_sha256": V3_SHA}
    nonce = hashlib.sha256(canonical({"candidate": "H06", **hashes})).hexdigest()
    receipt = seal({"schema": "h06-final-economic-authorization-v1", "candidate": "H06", **hashes,
                    "authorized": True, "economic_execution_authorized": True, "tuning_authorized": False,
                    "rerun_authorized": False, "promotion_authorized": False, "deterministic_nonce": nonce,
                    "integrity": {}})
    OUTPUT.write_text(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build()
