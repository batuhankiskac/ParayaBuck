"""Fail closed against the durable, archived canonical crypto control."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from research.crypto_exit_giveback_audit_v2.canonical_replay import digest, read, write

OUT = Path(__file__).resolve().parent


def check(raw: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "455_decisions": len(raw["decisions"]) == baseline["target_count"] == 455,
        "388_fills": len(raw["fills"]) == baseline["fill_count"] == 388,
        "targets_hash": digest([d["target"] for d in raw["decisions"]]) == baseline["target_hash"],
        "full_decision_and_hysteresis_evidence_hash": digest(raw["decisions"]) == baseline["decision_evidence_hash"],
        "fills_hash": digest(raw["fills"]) == baseline["fills_hash"],
        "orders_hash": digest([d["orders"] for d in raw["decisions"]]) == baseline["orders_hash"],
        "final_state_hash": digest(raw["final_state"]) == baseline["final_state_hash"],
        "terminal_events_hash": digest(raw["terminal_events"]) == baseline["terminal_hash"],
        "key_metrics": raw["metrics"] == baseline["metrics"],
        "full_payload_hash": digest(raw) == baseline["payload_hash"],
        "data_identity": raw["data"] == baseline["input"],
        "full_config": raw["config"] == baseline["full_config"],
        "hourly_scanner": raw["universe_timeframe"] == baseline["universe_timeframe"] == "1h",
        "daily_inputs_hash": digest(raw["daily_bars"]) == baseline["daily_bars_hash"],
        "universe_hash": raw["universe_hash"] == baseline["universe_hash"],
    }
    return {
        "status": "PASS" if all(checks.values()) else "CRYPTO_GIVEBACK_AUDIT_INCONCLUSIVE_PARITY_FAILURE",
        "checks": checks,
        "failed_checks": [k for k, v in checks.items() if not v],
        "authority": "docs/research/canonical_crypto_baseline.md",
        "archive_tag": "archive/maintenance/crypto-replay-parity-v1-2026-09",
        "archive_commit": "4b7eb4020896453a599c876092ff550216183d07",
        "production_source_sha": baseline["production_main_sha"],
        "post_baseline_main_sha": "b81e9d9fef3744723449f4c019f2a5dd75f2e2cb",
        "hashes": {
            k: baseline[k]
            for k in ("target_hash", "fills_hash", "final_state_hash", "decision_evidence_hash", "payload_hash")
        },
        "decisions": len(raw["decisions"]),
        "fills": len(raw["fills"]),
    }


def main() -> None:
    path = OUT / "CANONICAL_RAW.json.gz"
    result = check(read(path), read(OUT / "ARCHIVED_CANONICAL_RECEIPT.json"))
    result["fresh_gzip_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write(OUT / "CANONICAL_BASELINE_RECEIPT.json", result)
    if result["status"] != "PASS":
        raise SystemExit(result["status"])
    print("CANONICAL_PARITY_PASS")


if __name__ == "__main__":
    main()
