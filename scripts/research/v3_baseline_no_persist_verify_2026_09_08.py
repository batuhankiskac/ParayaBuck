"""Verify the frozen V3 baseline inputs without launching a run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXPECTED = {
    "data/historical/equity_pit_symmetric_2016_2026_v5.csv": "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139",
    "data/historical/equity_entity_lifetimes_full_v5.csv": "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596",
    "data/historical/equity_survivorship_full_corporate_actions_v5.csv": "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23",
    "config/settings.yaml": "414692bd0827240496253bdba67d9e1fca9748d7e29744be823fdc370fe8a353",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> dict[str, object]:
    hashes = {path: sha256(REPO / path) for path in EXPECTED}
    mismatches = {path: (actual, EXPECTED[path]) for path, actual in hashes.items() if actual != EXPECTED[path]}
    if mismatches:
        raise RuntimeError(json.dumps(mismatches, sort_keys=True))
    return {"status": "READY_FOR_INDEPENDENT_REVIEW", "launch": False, "persist": False, "state_path": None, "equity_ceiling": 0.55, "input_hashes": hashes}


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, sort_keys=True))
