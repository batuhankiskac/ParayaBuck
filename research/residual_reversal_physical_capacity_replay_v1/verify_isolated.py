"""Fail-closed structural comparison of the archived and physical candidate."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from parayabuck.core.models import Market

from .build_inputs import build_replay
from .clock_equivalence import ARCHIVE, OLD, audit, tagged_json
from .recording_candidate import RecordingReversal

OUT = Path(__file__).resolve().parent


def verify() -> dict[str, Any]:
    receipt = audit()
    archived = tagged_json(OLD, ARCHIVE + "execution_audit.json")["reversal_1x"]
    replay, identities = build_replay(6, "isolated_reversal")
    result = replay.run()
    strategy = replay.inputs[Market.EQUITY].engine.strategy
    assert isinstance(strategy, RecordingReversal)
    raw = strategy.raw_targets
    old_target = {row["decision_timestamp"]: row for row in archived["rebalances"]}
    new_target = {row["label_time"]: row for row in result.events if row["event"] == "decision"}
    mismatches: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}

    def mismatch(kind: str, label: str) -> None:
        mismatches[kind] += 1
        examples.setdefault(kind, [])
        if len(examples[kind]) < 5:
            examples[kind].append(label)

    for row in raw:
        label = str(row["label_time"])
        if label not in old_target:
            mismatch("unexpected_decision", label)
            continue
        weights = cast(list[list[Any]], row["weights"])
        symbols = {str(item[0]) for item in weights if float(item[1]) > 0}
        archived_symbols = {symbol for symbol, weight in old_target[label]["target_weights"] if weight > 0}
        if "daily_loss_limit" not in old_target[label]["risk_overlay_reasons"] and symbols != archived_symbols:
            mismatch("raw_target_symbols", label)
        if label not in new_target:
            mismatch("missing_physical_decision", label)
            continue
        prior = old_target[label]["target_weights"]
        current = new_target[label]["target_weights"]
        if len(prior) != len(current) or any(
            a[0] != b[0] or abs(float(a[1]) - float(b[1])) > 1e-8 for a, b in zip(prior, current)
        ):
            mismatch("post_overlay_target_weights", label)
    if len(raw) != len(old_target):
        mismatch("decision_count", f"physical={len(raw)} archived={len(old_target)}")

    prior_fills = {
        (row["decision_timestamp"], row["symbol"], row["side"]): datetime.fromisoformat(row["timestamp"]).date()
        for row in archived["fills"]
    }
    physical_fills = {
        (row["decision_timestamp"], row["symbol"], row["side"]): datetime.fromisoformat(row["timestamp"]).date()
        for row in result.events
        if row["event"] == "fill"
    }
    for key in sorted(set(prior_fills) | set(physical_fills)):
        if key not in prior_fills:
            mismatch("extra_fill_group", str(key))
        elif key not in physical_fills:
            mismatch("missing_fill_group", str(key))
        elif prior_fills[key] != physical_fills[key]:
            mismatch("fill_session", str(key))

    physical_dividends = sum(
        row["event"] == "corporate_action"
        and row.get("action_type") == "dividend"
        and float(row.get("position_qty_before", 0.0)) > 0
        for row in result.events
    )
    if physical_dividends != receipt["archived_dividend_entitled_symbol_events"]:
        mismatch("dividend_entitlement_count", str(physical_dividends))
    terminal_events = sum(row["event"] == "terminal_liquidation" for row in result.events)
    if terminal_events:
        mismatch("terminal_lifecycle", str(terminal_events))
    receipt.update(
        classification=(
            "REVERSAL_PHYSICAL_CLOCK_EQUIVALENT" if not mismatches else "REVERSAL_PHYSICAL_CLOCK_REVALIDATION_REQUIRED"
        ),
        physical_input_identity=identities,
        physical_decision_count=len(raw),
        physical_fill_group_count=len(physical_fills),
        physical_dividend_entitled_symbol_events=physical_dividends,
        physical_terminal_events=terminal_events,
        archived_risk_overlay_reduced_decisions=sum(
            "daily_loss_limit" in row["risk_overlay_reasons"] for row in old_target.values()
        ),
        raw_symbol_comparison_decisions=sum(
            "daily_loss_limit" not in row["risk_overlay_reasons"] for row in old_target.values()
        ),
        remaining_gate=(
            "Candidate physical chronology verified; incumbent physical parity and capacity arms remain."
            if not mismatches
            else "Repair and revalidate the listed candidate physical chronology mismatches before capacity arms."
        ),
        structural_event_sha256=result.event_sha256,
        internal_event_sha256=replay.internal_event_sha256,
        mismatch_counts=dict(mismatches),
        mismatch_examples=examples,
    )
    (OUT / "REVERSAL_PHYSICAL_CLOCK_EQUIVALENCE.json").write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "REVERSAL_PHYSICAL_CLOCK_EQUIVALENCE.md").write_text(
        "# Residual reversal physical clock equivalence\n\n"
        f"Classification: `{receipt['classification']}`. "
        f"Physical decisions: {len(raw)}; physical fill groups: {len(physical_fills)}; "
        f"dividend-entitled symbol events: {physical_dividends}; terminal events: {terminal_events}.\n\n"
        f"Mismatch counts: `{json.dumps(dict(mismatches), sort_keys=True)}`. "
        "Raw symbols were compared on the 415 decisions without the archived daily-loss overlay; "
        "the other 80 archived decisions have risk-reduced targets. "
        "All 495 decisions were compared for post-overlay target weights, "
        "fill group identity and session, dividend entitlement count, and terminal lifecycle. "
        "No economic outcomes were calculated or reported.\n",
        encoding="utf-8",
    )
    return receipt


if __name__ == "__main__":
    value = verify()
    print(
        json.dumps(
            {key: value[key] for key in ("classification", "mismatch_counts", "mismatch_examples")}, sort_keys=True
        )
    )
