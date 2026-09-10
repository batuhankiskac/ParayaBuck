#!/usr/bin/env python3
"""Parity-proven H14/current-V3 research artifact.

This file is intentionally outside the product/runtime surfaces.  It executes
the same V3 preparation and engine call as the frozen current-parity receipt,
then persists the otherwise missing dated NAV and weight series.  H14 is
joined only after all V3 component hashes and counts match the receipt.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import statistics
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import research_v3_equity_roadmap as roadmap  # noqa: E402

from parayabuck.config import load_config  # noqa: E402
from scripts.research_v3_current_parity_control import _result_components  # noqa: E402

CONTROL_RECEIPT = REPO / "data/research/equity_v3_current_parity_control_2026_08/control_r2.json"
H14_RECEIPT = REPO / "data/research/generated/h14_turn_of_month_2026_09_06_r4/h14_native_batch_receipt.json"
H14_MANIFEST = REPO / "data/research_manifests/h14_turn_of_month_protocol_2026_09_06_r4.json"
DEFAULT_OUTPUT = REPO / "data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/current_v3_daily_nav_comparability.json"
REQUIRED_COMPONENTS = (
    "universe_sha256",
    "target_sequence_sha256",
    "fill_sequence_sha256",
    "equity_curve_sha256",
    "terminal_events_sha256",
)


class H14V3ComparabilityError(ValueError):
    """Fail-closed research provenance or comparability failure."""


def _normalise(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, (tuple, list)):
        return [_normalise(v) for v in value]
    if dataclasses.is_dataclass(value):
        return _normalise(dataclasses.asdict(value))
    if hasattr(value, "value") and not isinstance(value, (str, bytes, int, float, bool)):
        return _normalise(value.value)
    if isinstance(value, float) and value == 0.0:
        return 0.0
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(_normalise(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise H14V3ComparabilityError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise H14V3ComparabilityError(f"JSON root is not an object: {path}")
    return value


def validate_control_receipt(path: Path = CONTROL_RECEIPT) -> dict[str, Any]:
    receipt = _read(path)
    claimed = receipt.get("receipt_sha256")
    payload = dict(receipt)
    payload.pop("receipt_sha256", None)
    if claimed != digest(payload):
        raise H14V3ComparabilityError("current V3 receipt digest mismatch")
    if receipt.get("receipt_name") != "CURRENT_PARITY_V3_CONTROL_R1":
        raise H14V3ComparabilityError("unexpected V3 receipt identity")
    if receipt.get("policy", {}).get("allocation") != 0.55 or receipt.get("policy", {}).get("terminal_policy") != "T1":
        raise H14V3ComparabilityError("current V3 policy is not the frozen 0.55/T1 policy")
    if any(key in receipt for key in ("curve_timestamps", "equity_curve", "daily_nav", "daily_returns")):
        raise H14V3ComparabilityError("aggregate receipt unexpectedly contains an unverified daily series")
    components = receipt.get("components")
    if not isinstance(components, dict) or any(not isinstance(components.get(k), str) for k in REQUIRED_COMPONENTS):
        raise H14V3ComparabilityError("aggregate receipt lacks complete component commitments")
    dataset = receipt.get("dataset")
    if not isinstance(dataset, dict):
        raise H14V3ComparabilityError("aggregate receipt lacks dataset pins")
    for key in ("bars_sha256", "lifetimes_sha256", "corporate_actions_sha256"):
        expected = dataset.get(key)
        if not isinstance(expected, str) or len(expected) != 64:
            raise H14V3ComparabilityError(f"missing dataset pin: {key}")
    return receipt


def validate_h14_inputs() -> dict[str, Any]:
    manifest = _read(H14_MANIFEST)
    for item in manifest.get("inputs", []):
        path = REPO / str(item["path"])
        if sha256_file(path) != item["sha256"]:
            raise H14V3ComparabilityError(f"H14 input hash mismatch: {path}")
    receipt = _read(H14_RECEIPT)
    if receipt.get("status") != "COMPLETE" or len(receipt.get("cells", [])) != 18:
        raise H14V3ComparabilityError("H14 receipt is not the frozen complete 18-cell matrix")
    if receipt.get("execution_manifest", {}).get("sha256") != sha256_file(H14_MANIFEST):
        raise H14V3ComparabilityError("H14 receipt is bound to a different execution manifest")
    return receipt


def _weight_history(result: Any) -> list[dict[str, Any]]:
    raw = getattr(result, "weight_history", ())
    rows: list[dict[str, Any]] = []
    for timestamp, weights in raw:
        rows.append({"timestamp": timestamp.isoformat(), "weights": _normalise(weights)})
    return rows


def run_exact_current_v3() -> tuple[dict[str, Any], Any, list[Any]]:
    """Run the frozen control call without the receipt runner's dirty-tree guard."""
    reference = validate_control_receipt()
    config = load_config(str(REPO / "config/settings.yaml"))
    bars = roadmap.load_bars(roadmap.DATASET_V5)
    terminal_map, _ = roadmap.load_lifetimes()
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
    components = _result_components(result, engine, window)
    if components != reference["components"]:
        raise H14V3ComparabilityError("current V3 component parity failed")
    expected_counts = reference["counts"]
    actual_counts = {
        "rebalance_decisions": len(result.rebalances),
        "fills": len(result.fills),
        "terminal_events": len(engine.terminal_events_fired),
        "incomplete_strategy_batches": 0,
    }
    if actual_counts != expected_counts:
        raise H14V3ComparabilityError(f"current V3 count parity failed: {actual_counts!r}")
    if roadmap.full_stats(result) != reference["metrics"]:
        raise H14V3ComparabilityError("current V3 metric parity failed")
    daily = [
        {"timestamp": timestamp.isoformat(), "date": timestamp.astimezone(UTC).date().isoformat(), "nav": float(nav)}
        for timestamp, nav in zip(result.curve_timestamps, result.equity_curve)
    ]
    if len(daily) != 2658 or any(not math.isfinite(row["nav"]) or row["nav"] <= 0 for row in daily):
        raise H14V3ComparabilityError("current V3 daily series is incomplete or non-finite")
    series = {
        "schema_version": "h14-v3-daily-nav-v1",
        "parity_status": "PASS",
        "current_control_receipt": {
            "path": str(CONTROL_RECEIPT.relative_to(REPO)),
            "sha256": sha256_file(CONTROL_RECEIPT),
            "receipt_sha256": reference["receipt_sha256"],
        },
        "source_pins": {
            "runner": "scripts/research_v3_current_parity_control.py",
            "runner_sha256": reference["script_sha256"],
            "roadmap": "scripts/research_v3_equity_roadmap.py",
            "bars": "data/historical/equity_pit_symmetric_2016_2026_v5.csv",
            "bars_sha256": reference["dataset"]["bars_sha256"],
            "lifetimes": "data/historical/equity_entity_lifetimes_full_v5.csv",
            "lifetimes_sha256": reference["dataset"]["lifetimes_sha256"],
            "corporate_actions": "data/historical/equity_survivorship_full_corporate_actions_v5.csv",
            "corporate_actions_sha256": reference["dataset"]["corporate_actions_sha256"],
            "config_sha256": sha256_file(REPO / "config/settings.yaml"),
            "policy": reference["policy"],
        },
        "counts": {"bars_loaded": len(bars), "pit_snapshots": len(window.universe.snapshots), "corporate_actions": len(actions), "daily_nav_rows": len(daily), **actual_counts},
        "components": components,
        "metrics": reference["metrics"],
        "daily_nav": daily,
        "weight_history": _weight_history(result),
    }
    return series, result, bars


def _daily_map(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Collapse same-date terminal marks using the engine's last-value rule."""
    result: dict[str, float] = {}
    for row in rows:
        day = str(row["date"])
        # The native engine can append a same-session post-liquidation mark.
        # Keep its final value; never average, interpolate, or choose by date.
        result[day] = float(row["nav"])
    return result


def _returns(values: list[float]) -> list[float]:
    return [right / left - 1.0 for left, right in zip(values, values[1:])]


def _max_drawdown(values: list[float]) -> float:
    high = values[0]
    worst = 0.0
    for value in values:
        high = max(high, value)
        worst = min(worst, value / high - 1.0)
    return abs(worst)


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    try:
        return statistics.correlation(left, right)
    except statistics.StatisticsError:
        return None


def _forward_filled_weights(rows: list[dict[str, Any]], dates: list[str]) -> dict[str, dict[str, Any]]:
    """Expand rebalance-time weights to each marked session without inference."""
    events: dict[str, dict[str, Any]] = {}
    for row in rows:
        events[str(row["timestamp"])[:10]] = row.get("weights", {})
    current: dict[str, Any] = {}
    expanded: dict[str, dict[str, Any]] = {}
    for day in sorted(dates):
        if day in events:
            value = events[day]
            current = dict(value) if isinstance(value, dict) else {}
        expanded[day] = current
    return expanded


def evaluate_h14(series: dict[str, Any], h14: dict[str, Any], bars: list[Any]) -> dict[str, Any]:
    """Run the predeclared 18 H14 cells after V3 parity, with fixed join rules."""
    v3_rows = series["daily_nav"]
    v3 = _daily_map(v3_rows)
    h14_cells = h14["cells"]
    controls: dict[str, str] = {}
    # Constants are frozen before reading any cell outcome.
    v3_capital_share = 0.95
    h14_capital_share = 0.05
    total_positions_cap = 6
    gross_cap = 0.60
    cash_floor = 0.40
    outcomes: list[dict[str, Any]] = []
    # Build the fixed SPY regime map once.  Repeating this scan per cell would
    # be equivalent but needlessly rescans the 11m-row source 18 times.
    regime = roadmap.spy_daily_regimes(bars)
    v3_timestamp_by_date = {row["date"]: row["timestamp"] for row in v3_rows}
    for cell in h14_cells:
        hrows = cell.get("equity_curve", [])
        if not hrows:
            raise H14V3ComparabilityError("H14 cell has no NAV rows")
        hmap = {str(row["timestamp"])[:10]: float(row["nav"]) for row in hrows}
        common = sorted(set(v3) & set(hmap))
        if len(common) < 2:
            raise H14V3ComparabilityError(f"insufficient V3/H14 join dates for {cell['cell']}/{cell['mode']}")
        vvals = [v3[day] / v3[common[0]] for day in common]
        hvals = [hmap[day] / hmap[common[0]] for day in common]
        joint = [100000.0 * (v3_capital_share * v + h14_capital_share * h) for v, h in zip(vvals, hvals)]
        vr = _returns(vvals)
        hr = _returns(hvals)
        jr = _returns(joint)
        # H14 marks are not reinterpreted; only its explicit SPY weight makes it active.
        marked = _forward_filled_weights(cell.get("marked_positions", []), common)
        wh = _forward_filled_weights(series.get("weight_history", []), common)
        feasible = 0
        active_days = 0
        rejected_days = 0
        gross_max = 0.0
        v3_position_max = 0
        for day in common:
            vweights = wh.get(day, {})
            if isinstance(vweights, dict):
                v3_gross = sum(float(value) for value in vweights.values() if isinstance(value, (int, float)))
                v3_positions = sum(float(value) > 0 for value in vweights.values() if isinstance(value, (int, float)))
            else:
                v3_gross = 0.0
                v3_positions = 0
            hweights = marked.get(day, {})
            h_active = isinstance(hweights, dict) and float(hweights.get("SPY", 0.0) or 0.0) > 0
            active_days += h_active
            v3_position_max = max(v3_position_max, int(v3_positions))
            joint_gross = v3_capital_share * v3_gross + (h14_capital_share if h_active else 0.0)
            gross_max = max(gross_max, joint_gross)
            ok = (joint_gross <= gross_cap + 1e-12 and 1.0 - joint_gross >= cash_floor - 1e-12 and int(v3_positions) + int(h_active) <= total_positions_cap)
            feasible += ok
            rejected_days += not ok
        regime_pairs: dict[str, dict[str, float]] = {}
        day_regimes = {day: regime.get(datetime.fromisoformat(v3_timestamp_by_date[day]), "UNKNOWN") for day in common if day in v3_timestamp_by_date}
        for regime_name in sorted(set(day_regimes.values())):
            vr_reg: list[float] = []
            hr_reg: list[float] = []
            for idx, day in enumerate(common[1:], start=1):
                if regime_name == day_regimes.get(day, "UNKNOWN"):
                    vr_reg.append(vr[idx - 1])
                    hr_reg.append(hr[idx - 1])
            regime_pairs[regime_name] = {"days": len(vr_reg), "v3_mean_return": round(statistics.fmean(vr_reg), 8) if vr_reg else None, "h14_mean_return": round(statistics.fmean(hr_reg), 8) if hr_reg else None}
        key = f"{cell['cell']}/{cell['mode']}/{cell['cost_multiplier']}x"
        controls[key] = "fixed 95% V3 + 5% H14 normalized sleeve; H14 active only from explicit marked_positions.SPY; no tuning"
        outcomes.append({
            "cell": cell["cell"], "mode": cell["mode"], "cost_multiplier": cell["cost_multiplier"],
            "common_dates": len(common), "dropped_h14_dates": len(set(hmap) - set(common)),
            "joint_total_return": round(joint[-1] / joint[0] - 1.0, 8), "joint_max_drawdown": round(_max_drawdown(joint), 8),
            "joint_worst_daily_return": round(min(jr), 8), "v3_h14_daily_return_correlation": _pearson(vr, hr),
            "shared_capital": {"feasible_days": feasible, "rejected_days": rejected_days, "active_h14_days": active_days, "v3_max_positions": v3_position_max, "joint_max_gross": round(gross_max, 8), "gross_cap": gross_cap, "cash_floor": cash_floor, "total_positions_cap": total_positions_cap},
            "regime": regime_pairs,
        })
    return {"rules": {"v3_capital_share": v3_capital_share, "h14_capital_share": h14_capital_share, "equity_cap": 0.55, "crypto_cap": 0.05, "gross_cap": gross_cap, "cash_floor": cash_floor, "max_crypto_positions": 2, "total_positions_cap": total_positions_cap, "cost_multipliers": [1.0, 2.0, 3.0]}, "controls": controls, "cells": outcomes}


def build_artifact(output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    reference = validate_control_receipt()
    h14 = validate_h14_inputs()
    series, _result, bars = run_exact_current_v3()
    payload: dict[str, Any] = {
        "schema_version": "h14-v3-comparability-research-v1",
        "status": "H14_PORTFOLIO_CONTRIBUTION_TESTED_BUT_INFEASIBLE_REMAINS_DIAGNOSTIC",
        "economic_claim": False,
        "promotion": False,
        "parity": {"status": series["parity_status"], "components_match": True, "counts_match": True, "metrics_match": True, "current_control_receipt_sha256": sha256_file(CONTROL_RECEIPT), "current_control_receipt_digest": reference["receipt_sha256"]},
        "h14": {"receipt": str(H14_RECEIPT.relative_to(REPO)), "receipt_sha256": sha256_file(H14_RECEIPT), "manifest": str(H14_MANIFEST.relative_to(REPO)), "manifest_sha256": sha256_file(H14_MANIFEST), "matrix_cells": 18},
        "join": {"series_semantics": "same-date end-of-session normalized NAV; no interpolation; unmatched H14 endpoints dropped", "v3_daily_rows": len(series["daily_nav"]), "v3_first": series["daily_nav"][0]["timestamp"], "v3_last": series["daily_nav"][-1]["timestamp"]},
        "evaluation": evaluate_h14(series, h14, bars),
        "disposition": {
            "eligible_for_next_research_stage": False,
            "decision": "REMAINS_DIAGNOSTIC",
            "reasons": [
                "candidate/calendar H14 active days conflict with the frozen total-positions cap when current V3 already holds six positions",
                "the frozen H14 matrix specifies full/recent cells and cost stress but no walk-forward or holdout cells",
                "descriptive joint NAV, drawdown, tail and regime metrics do not establish a promotion or portfolio contribution claim",
            ],
            "walk_forward": "NOT_SPECIFIED_IN_FROZEN_H14_PROTOCOL",
            "holdout": "NOT_SPECIFIED_IN_FROZEN_H14_PROTOCOL",
        },
        "source_pins": series["source_pins"],
        "counts": series["counts"],
        "components": series["components"],
        "metrics": series["metrics"],
        "daily_nav": series["daily_nav"],
        "weight_history": series["weight_history"],
    }
    payload["artifact_sha256"] = digest(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build_artifact(args.output if args.output.is_absolute() else REPO / args.output)
    print(json.dumps({"status": payload["status"], "artifact_sha256": payload["artifact_sha256"], "daily_nav_rows": payload["counts"]["daily_nav_rows"], "h14_cells": payload["h14"]["matrix_cells"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
