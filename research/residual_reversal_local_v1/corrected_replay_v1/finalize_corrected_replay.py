"""Finalize serialized corrected-replay outputs without rerunning economics."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = Path(__file__).resolve().parent
RESEARCH_DIR = OUTPUT_DIR.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.residual_reversal_local_v1 import run_campaign as original
from research.residual_reversal_local_v1.corrected_replay_v1 import corrected_replay as runner


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _cost_keyed(payload: dict[str, Any]) -> dict[int, Any]:
    return {int(key): value for key, value in payload["costs"].items()}


def main() -> int:
    manifest = _load(OUTPUT_DIR / "replay_manifest.json")
    if manifest.get("economic_results_generated") is not True:
        raise RuntimeError("Cannot finalize before the one-time economic replay is complete")
    verification = _load(OUTPUT_DIR / "corrected_independent_verification.json")
    if verification.get("status") != "CORRECTED_REPLAY_VALID":
        raise RuntimeError("Cannot interpret frozen gates from an invalid corrected replay")

    v3_by_cost = _cost_keyed(_load(OUTPUT_DIR / "v3_control.json"))
    reversal = _cost_keyed(_load(OUTPUT_DIR / "standalone_reversal.json"))
    treatment = _cost_keyed(_load(OUTPUT_DIR / "treatment.json"))
    cost_stress = _load(OUTPUT_DIR / "cost_stress.json")
    diversification = _load(OUTPUT_DIR / "diversification.json")
    robustness = _load(OUTPUT_DIR / "robustness.json")
    coverage = _load(OUTPUT_DIR / "decision_coverage.json")
    sleeve = _load(OUTPUT_DIR / "sleeve_attribution.json")
    lifecycle = _load(OUTPUT_DIR / "lifecycle_audit.json")
    receipt = _load(RESEARCH_DIR / "DATA_RECEIPT.json")

    gates = original._gate_table(reversal, v3_by_cost[1], treatment, diversification, robustness)
    decision = original._verdict(gates, receipt)
    replay_validity = "CORRECTED_REPLAY_VALID"
    runner._write_json(OUTPUT_DIR / "gate_table.json", gates)
    runner._write_json(
        OUTPUT_DIR / "decision.json",
        {"decision": decision, "replay_validity": replay_validity, "gates": gates},
    )
    manifest.update(
        {
            "replay_validity": replay_validity,
            "decision": decision,
            "verification_status": verification.get("status"),
            "finalization_mode": "serialized-results-only; no economic replay",
        }
    )
    runner._write_json(OUTPUT_DIR / "replay_manifest.json", manifest)
    (OUTPUT_DIR / "FINAL_CORRECTED_REPLAY_REPORT.md").write_text(
        runner._build_final_report(
            decision=decision,
            replay_validity=replay_validity,
            start_head=manifest["head_sha_at_replay_start"],
            final_sha="pending-uncommitted-research-branch",
            manifest=manifest,
            coverage=coverage,
            observation=_load(OUTPUT_DIR / "observation_sets.json"),
            sleeve=sleeve,
            lifecycle=lifecycle,
            v3=v3_by_cost[1],
            reversal=reversal,
            treatment=treatment,
            cost_stress=cost_stress,
            diversification=diversification,
            robustness=robustness,
            gates=gates,
            verification=verification,
        ),
        encoding="utf-8",
    )
    print(f"Corrected replay validity: {replay_validity}")
    print(f"Economic decision: {decision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
