"""Gate and document the frozen-environment crypto baseline without changing trading."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from maintenance.crypto_replay_parity_v1.runner import digest, read, write

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = "754f4060b1eb38593201b2a269e9593d083eb534"
HISTORICAL = "0d792dcd4468971701517b06dd8c10d4710f2cf3"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    a_path = OUT / "CURRENT_HOURLY_REPLAY.json.gz"
    b_path = OUT / "CURRENT_HOURLY_REPLAY_B.json.gz"
    a, b = read(a_path), read(b_path)
    old = read(OUT / "HISTORICAL_REPLAY.json.gz")
    historical = read(OUT / "HISTORICAL_CONTROL.json")
    verification = read(OUT / "INDEPENDENT_VERIFICATION.json")
    universe_verification = read(OUT / "UNIVERSE_HOURLY_INDEPENDENT_VERIFICATION.json")
    assert a == b and a_path.read_bytes() == b_path.read_bytes(), "Current repeat not byte-identical"
    assert old["historical_hashes"] == historical["full_window"]["hashes_production"], "Historical reproduction failed"
    assert old["universe_hash"] == historical["pit_hash"], "Historical universe reproduction failed"
    assert old["data"]["sha256"] == a["data"]["sha256"] and old["hashes"]["daily_bars"] == a["hashes"]["daily_bars"]
    assert verification["status"] == "PASS" and verification["replay_file_sha256"] == file_hash(a_path)
    assert universe_verification["status"] == "PASS" and universe_verification["input_sha256"] == file_hash(a_path)
    assert a["universe_timeframe"] == "1h"
    assert a["config"]["strategy"]["crypto"]["name"] == "crypto_low_variance_hysteresis_v1"
    receipt: dict[str, Any] = {
        "schema": "canonical-crypto-baseline-v2",
        "status": "CRYPTO_CANONICAL_REPLAY_VALID",
        "production_main_sha": BASE,
        "historical_reproduction_source_sha": HISTORICAL,
        "replay_mode": "CRYPTO_STRATEGY_ISOLATED_CANONICAL",
        "shared_account_replay": False,
        "initial_state": {"cash": 100000.0, "positions": {}, "pending_orders": []},
        "strategy": a["config"]["strategy"]["crypto"],
        "strategy_version": "1.0.0",
        "full_config": a["config"],
        "config_sha256": a["config_sha256"],
        "input": a["data"],
        "window": a["window"],
        "universe_timeframe": "1h",
        "strategy_timeframe": "1d",
        "daily_bar_count": len(a["daily_bars"]),
        "daily_bars_hash": a["hashes"]["daily_bars"],
        "universe_hash": a["universe_hash"],
        "universe_snapshot_count": len(a["universe"]["snapshots"]),
        "universe_policy": "unchanged historical scanner on hourly bars; current production observation visibility; daily strategy bars aggregated from same frozen hourly data",
        "authority_rule": "preserve frozen historical scanner environment and current configured production semantics, not whichever return is larger",
        "engine": a["engine"],
        "target_count": len(a["decisions"]),
        "target_hash": digest([d["target"] for d in a["decisions"]]),
        "decision_evidence_hash": a["hashes"]["decisions"],
        "order_count": sum(len(d["orders"]) for d in a["decisions"]),
        "orders_hash": digest([d["orders"] for d in a["decisions"]]),
        "fill_count": len(a["fills"]),
        "fills_hash": a["hashes"]["fills"],
        "nav_observation_count": len(a["snapshots"]),
        "nav_hash": digest(a["equity_curve"]),
        "final_state": a["final_state"],
        "final_state_hash": a["hashes"]["final_state"],
        "terminal_events": a["terminal_events"],
        "terminal_hash": a["hashes"]["terminal_events"],
        "metrics": a["metrics"],
        "payload_hash": digest(a),
        "gzip_bytes_sha256": file_hash(a_path),
        "repeat_gzip_bytes_sha256": file_hash(b_path),
        "repeated_bytes_identical": True,
        "historical_reproduction_six_hashes_match": True,
        "historical_status": "SUPERSEDED_HISTORICAL_CONTROL",
        "independent_verification": verification,
        "independent_universe_verification": universe_verification,
        "limits": [
            "Expanded CSV exact provider receipt unavailable; no claim of source completeness or broker historical tradability.",
            "PIT tradability is the existing configured-pool and trailing-bar-availability proxy.",
            "Hourly scanner observation alpha falls back when daily ranker receives hourly history; this existing adapter behavior is frozen, not repaired or promoted to runtime parity.",
            "Offline drawdown halt is shadowed; isolated replay is not a shared PAPER account or evidence of LIVE readiness.",
            "No giveback interpretation or alternative exit tested.",
        ],
        "noncanonical_comparator": "CURRENT_REPLAY_A.json.gz uses current public daily scanner defaults and changes eligibility; not promoted",
    }
    write(OUT / "CANONICAL_REPLAY_RECEIPT.json", receipt)
    decision = {
        "primary_conclusion": "CRYPTO_HISTORICAL_BASELINE_SUPERSEDED",
        "root_cause_labels": ["STALE_HISTORICAL_BASELINE", "UNIVERSE_DRIFT"],
        "universe_drift_scope": "effective observation membership and capacity-limited strategy visibility, NOT scanner eligible pool or source asset availability",
        "causal_commit": "ac58f34",
        "first_context_difference": "2021-04-01T00:00:00+00:00",
        "first_eligible_context_difference": "2023-03-28T00:00:00+00:00",
        "first_rank_difference": "2023-04-09T00:00:00+00:00",
        "first_economic_target_difference": "2023-05-11T00:00:00+00:00",
        "first_economic_fill_nav_difference": "2023-05-12T00:00:00+00:00",
        "ruled_out": [
            "DATASET_DRIFT",
            "TIME_BOUNDARY_DRIFT",
            "HYSTERESIS_SEMANTICS_DRIFT",
            "COST_MODEL_DRIFT",
            "SHARED_ACCOUNT_VS_ISOLATED_MISMATCH",
        ],
        "config_differences": "strategy promotion/observation identity and unrelated equity/observability defaults; instantiated T1 parameters and relevant crypto sizing/cost/risk controls unchanged",
        "serialization_only": False,
        "secondary_history_change": "a203799 removes duplicate final NAV timestamp and preserves explicit liquidation evidence; not the first economic divergence",
        "production_correctness_fix_required": False,
        "production_files_changed": False,
        "historical_status": "SUPERSEDED_HISTORICAL_CONTROL",
        "new_baseline_status": "CRYPTO_CANONICAL_REPLAY_VALID",
        "handoff": "CRYPTO_GIVEBACK_AUDIT_READY_FOR_RERUN",
        "previous_crypto_giveback_conclusion_valid": False,
        "previous_quarantined_path_matches_canonical": True,
        "no_new_giveback_interpretation": True,
    }
    write(OUT / "ROOT_CAUSE_DECISION.json", decision)
    doc = f"""# Canonical crypto replay baseline v2

Status: **CRYPTO_CANONICAL_REPLAY_VALID**. Historical control: **SUPERSEDED_HISTORICAL_CONTROL**. Governing conclusion: **CRYPTO_HISTORICAL_BASELINE_SUPERSEDED**.

This is a candidate-neutral, isolated offline replay contract, not a strategy promotion or a PAPER/LIVE account claim. Production source is `{BASE}`. Parameters, allocation, tradable candidate pool, scanner configuration, costs and exits are unchanged.

## Authority and environment

Retain the established hourly scanner input and aggregate those same frozen bytes into complete UTC daily strategy bars. Do not replace this environment with the public helper's daily-scanner default: that is a distinct, separately measured adapter and changes historical eligibility. Environment authority was chosen to preserve the historical contract, not by comparing returns.

- Strategy: crypto_low_variance_hysteresis_v1 v1.0.0; 22 daily simple returns, sample standard deviation, ascending rank; top two, held-name retention through rank four; epoch-aligned 96-hour decision blocks; equal raw weights; 5% sleeve cap.
- Mode: CRYPTO_STRATEGY_ISOLATED_CANONICAL; $100,000 cash, no positions/pending orders. Equity does not occupy slots. This is not TOTAL_ACCOUNT_SHARED_CANONICAL.
- Window: 2021-04-01 00:00 UTC through 2026-07-31 23:59:59 UTC; earlier rows are warmup. Same historical endpoint, no appended-future-data comparison.
- Input: data/historical/crypto_expanded_1h.csv, SHA256 `{a["data"]["sha256"]}`; 705,541 hourly rows, 28,890 complete daily bars including warmup. Daily labels denote bucket starts; decisions consume the completed day's data and fill on a later bar's open.
- Costs: 2bps slippage, 2bps full spread (half applied each side), 25bps crypto taker fee, zero fixed commission, 1x cost multiplier. Volume participation uses strictly order-known positive-volume history; exact resolved settings are in the receipt.
- Offline drawdown enforcement is shadow-only; daily-loss restrictions still apply. Final holdings are forcibly liquidated and costs charged; final NAV timestamp is unique.
- Original provider-download receipt for the expanded file and historical broker tradability metadata are unavailable. Authority is reproducibility on these pinned bytes under the documented bar-availability proxy, not proof of a complete survivorship-free provider dataset.
- Current hourly adapter supplies hourly scanner history to a daily observation ranker, which falls back to scanner ordering; this behavior is explicit and frozen. Do not claim it reproduces the runtime's separately warmed daily observation ranking. Changing this environment requires a separately authorized contract change.

## Identity and verification

| Quantity | Value |
| --- | --- |
| Target decisions | {len(a["decisions"])} |
| Fills | {len(a["fills"])} |
| NAV observations | {len(a["snapshots"])} |
| Ending equity | ${a["metrics"]["ending_equity"]:,.8f} |
| Target SHA256 | `{receipt["target_hash"]}` |
| Fill SHA256 | `{receipt["fills_hash"]}` |
| Final-state SHA256 | `{receipt["final_state_hash"]}` |
| Normalized daily input SHA256 | `{receipt["daily_bars_hash"]}` |
| Full deterministic payload SHA256 | `{receipt["payload_hash"]}` |

Two fresh runs are byte-identical after deterministic gzip (mtime=0) and canonical JSON serialization. Independent code imports no production scoring, strategy, risk, execution or accounting functions. It reconstructs every decision, rank, retention, vacancy, target, order, fill, cost, cash/position/NAV point and final state. Separate independent eligibility verification covers all 2,037 scanner snapshots. See the receipts for tolerances and scanner ordering scope.

## Why the old control is superseded

The archived source `{HISTORICAL}` exactly reproduces all six historical hashes and the original universe hash on the same input bytes. Its 453 decisions/454 fills are valid evidence of that old implementation, not today's contract. Commit ac58f34 restricts strategy history to effective observations; the old engine ranked symbols outside the stream. On 2023-05-11 the old path selected BTC+SHIB while the current path selected BTC+ETH, followed by different fills on May12. This is an upstream visibility change, not six independent alpha/execution/cost/NAV bugs. Later terminal-bookkeeping corrections also make legacy curve/final-state serialization inappropriate as a current equality oracle.

Full evidence: [maintenance audit](../../maintenance/crypto_replay_parity_v1/FINAL_PARITY_REPORT.md), [receipt](../../maintenance/crypto_replay_parity_v1/CANONICAL_REPLAY_RECEIPT.json), [first divergence](../../maintenance/crypto_replay_parity_v1/FIRST_DIVERGENCE_AUDIT.json).

Reproduce from repository root using the existing pinned dataset:

```sh
.venv/bin/python maintenance/crypto_replay_parity_v1/runner.py --universe-timeframe 1h --out maintenance/crypto_replay_parity_v1/CURRENT_HOURLY_REPLAY.json.gz
.venv/bin/python maintenance/crypto_replay_parity_v1/runner.py --universe-timeframe 1h --out maintenance/crypto_replay_parity_v1/CURRENT_HOURLY_REPLAY_B.json.gz
```

Run both independent verifier CLIs and comparison.py as documented in FINAL_PARITY_REPORT.md before establish_baseline.py. The baseline gate refuses unequal repeated outputs, unmatched historical reproduction, or failed/mismatched verifier receipts.

Handoff: **CRYPTO_GIVEBACK_AUDIT_READY_FOR_RERUN**. The previous quarantined current-code path agrees with this baseline's output, but its profit-retention analysis must be rerun and independently verified in a separate task. No exit recommendation follows from this maintenance work.
"""
    path = ROOT / "docs/research/canonical_crypto_baseline.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc)
    print(decision["primary_conclusion"], decision["handoff"])


if __name__ == "__main__":
    main()
