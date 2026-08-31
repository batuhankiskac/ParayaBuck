#!/usr/bin/env python3
"""Write a clean, hash-addressed current-parity V3 control receipt.

This research-only runner intentionally does not update a tracked manifest or
register a strategy.  It is the provenance gate between the committed parity
implementation and any later candidate research.  Run it twice with different
output paths and compare the component hashes; any mismatch invalidates the
research cycle.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import research_v3_equity_roadmap as roadmap  # noqa: E402

from parayabuck.config import load_config  # noqa: E402

DEFAULT_OUTPUT = REPO / "data/research/equity_v3_current_parity_control_2026_08/control.json"


def _git_dirty() -> list[str]:
    output = subprocess.check_output(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=REPO,
        text=True,
    )
    return [line for line in output.splitlines() if line]


def _normalise(value: Any) -> Any:
    """Convert receipt values to deterministic JSON-compatible structures."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return _normalise(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _normalise(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list)):
        return [_normalise(item) for item in value]
    if isinstance(value, float) and value == 0.0:
        # Avoid a platform-dependent ``-0.0`` in hashes.
        return 0.0
    return value


def canonical_digest(value: Any) -> str:
    payload = json.dumps(_normalise(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()


def _result_components(result: object, engine: object, window: roadmap.PreparedWindow) -> dict[str, str]:
    rebalances = getattr(result, "rebalances")
    fills = getattr(result, "fills")
    curve = {
        "timestamps": getattr(result, "curve_timestamps"),
        "equity": getattr(result, "equity_curve"),
    }
    terminal = getattr(engine, "terminal_events_fired", ())
    return {
        "universe_sha256": canonical_digest(window.universe.to_dict()),
        "target_sequence_sha256": canonical_digest(rebalances),
        "fill_sequence_sha256": canonical_digest(fills),
        "equity_curve_sha256": canonical_digest(curve),
        "terminal_events_sha256": canonical_digest(terminal),
    }


def run(output: Path = DEFAULT_OUTPUT) -> dict[str, object]:
    dirty_before = _git_dirty()
    if dirty_before:
        raise SystemExit(
            f"current-parity control requires a clean tracked worktree; dirty paths: {', '.join(dirty_before)}"
        )
    config = load_config(str(REPO / "config/settings.yaml"))
    if float(config.strategy.equity.maximum_allocation) != roadmap.CURRENT_EQUITY_ALLOCATION:
        raise SystemExit("current-parity control requires the declared 0.55 equity allocation")

    bars = roadmap.load_bars(roadmap.DATASET_V5)
    terminal_map, _lifetimes = roadmap.load_lifetimes()
    actions = roadmap.load_corporate_actions_all()
    window = roadmap.prepare_window(config, bars, key="current-parity-control", start=None, end=roadmap.EVAL_END)
    strategy = roadmap.build_strategy("control")
    engine = roadmap.make_engine(config, strategy, window, terminal_map=terminal_map)
    result = engine.run(
        window.filtered,
        warmup=window.warmup,
        liquidate_at_end=True,
        corporate_actions=list(actions),
        prepared_inputs=window.prepared,
        numeric_history=window.numeric_history,
    )
    if not result.equity_curve:
        raise SystemExit("current-parity control produced an empty equity curve")
    dirty_after = _git_dirty()
    if dirty_after:
        raise SystemExit(f"control run changed tracked worktree paths: {', '.join(dirty_after)}")

    payload: dict[str, object] = {
        "schema_version": "1.0.0",
        "receipt_name": "CURRENT_PARITY_V3_CONTROL_R1",
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "script_sha256": roadmap.sha256_of_file(Path(__file__)),
        "config_digest_sha256": roadmap.config_digest(config),
        "dataset": {
            "bars_sha256": roadmap.sha256_of_file(roadmap.DATASET_V5),
            "lifetimes_sha256": roadmap.sha256_of_file(roadmap.LIFETIMES_V5),
            "corporate_actions_sha256": roadmap.sha256_of_file(roadmap.CA_V5),
        },
        "policy": {
            "allocation": roadmap.CURRENT_EQUITY_ALLOCATION,
            "terminal_policy": "T1",
            "evaluation_end": roadmap.EVAL_END.isoformat(),
        },
        "strategy": {
            "name": strategy.name,
            "version": strategy.version,
            "parameters": dict(strategy.parameter_tuple()),
        },
        "metrics": roadmap.full_stats(result),
        "counts": {
            "rebalance_decisions": len(result.rebalances),
            "fills": len(result.fills),
            "terminal_events": len(engine.terminal_events_fired),
            "incomplete_strategy_batches": 0,
        },
        "components": _result_components(result, engine, window),
        "dirty_paths_before": dirty_before,
        "dirty_paths_after": dirty_after,
    }
    payload["receipt_sha256"] = canonical_digest(payload)
    output = output if output.is_absolute() else REPO / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = run(args.output)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
