"""No-return, target-only capacity diagnostic; not a physical shared-account replay."""

import csv
import gzip
import json
import subprocess
from bisect import bisect_right
from pathlib import Path
from typing import Any, TypedDict

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OLD = "archive/research/residual-reversal-local-v1-2026-09"


class Admission(TypedDict):
    slots: int
    accepted: dict[str, float]
    denials: dict[str, int]
    selected_equity: list[str]


def archived(path: str) -> Any:
    return json.loads(subprocess.check_output(["git", "show", f"{OLD}:{path}"], cwd=ROOT))


def arbitrate(
    components: dict[str, dict[str, float]], ranks: dict[str, list[str]], crypto: list[str], cap: int
) -> Admission:
    """Crypto-first, then alternate source fulfillment by admitted exposure ratio."""
    selected = {("crypto", symbol) for symbol in crypto[:2]}
    accepted = {"v3": 0.0, "reversal": 0.0, "crypto": 0.05 if crypto else 0.0}
    requested = {source: sum(parts[source] for parts in components.values()) for source in ("v3", "reversal")}
    ordered = {
        source: [symbol for symbol in ranks[source] if components[symbol][source] > 0] for source in ("v3", "reversal")
    }
    indices = {"v3": 0, "reversal": 0}
    denials = {"v3": 0, "reversal": 0, "crypto": max(0, len(crypto) - 2)}
    while any(indices[source] < len(ordered[source]) for source in ordered):
        available = [source for source in ordered if indices[source] < len(ordered[source])]
        source = min(
            available,
            key=lambda name: (accepted[name] / requested[name] if requested[name] else 1, 0 if name == "v3" else 1),
        )
        symbol = ordered[source][indices[source]]
        indices[source] += 1
        key = ("equity", symbol)
        if key not in selected and len(selected) >= cap:
            denials[source] += 1
            continue
        selected.add(key)
        accepted[source] += components[symbol][source]
    return {
        "slots": len(selected),
        "accepted": accepted,
        "denials": denials,
        "selected_equity": sorted(symbol for market, symbol in selected if market == "equity"),
    }


def main() -> None:
    old = archived("research/residual_reversal_local_v1/corrected_replay_v1/sleeve_attribution.json")
    assert isinstance(old, dict)
    rows = old["by_cost"]["1x"]["per_decision"]
    with gzip.open(
        ROOT / "maintenance/crypto_canonical_causal_execution_v1/CORRECTED_REPLAY_A.json.gz", "rt"
    ) as handle:
        crypto = json.load(handle)["decisions"]
    crypto_times = [row["decision_at"] for row in crypto]
    output: list[dict[str, Any]] = []
    overlap_total = overlap_decisions = cap8_extra = 0
    for row in rows:
        components = row["source_contributions"]
        stamp = row["decision_timestamp"]
        index = bisect_right(crypto_times, stamp) - 1
        crypto_weights = crypto[index]["alpha_target"]["weights"] if index >= 0 else []
        crypto_symbols = [weight["symbol"] for weight in crypto_weights]
        overlap = set(row["raw_v3_target_symbols"]) & set(row["raw_reversal_target_symbols"])
        overlap_total += len(overlap)
        overlap_decisions += bool(overlap)
        ranks = {"v3": row["raw_v3_target_symbols"], "reversal": row["raw_reversal_target_symbols"]}
        six = arbitrate(components, ranks, crypto_symbols, 6)
        eight = arbitrate(components, ranks, crypto_symbols, 8)
        cap8_extra += len(set(eight["selected_equity"]) - set(six["selected_equity"]))
        output.append(
            {
                "decision_label": stamp,
                "v3_symbols": json.dumps(ranks["v3"]),
                "reversal_symbols": json.dumps(ranks["reversal"]),
                "crypto_symbols": json.dumps(crypto_symbols),
                "crypto_weights": json.dumps(crypto_weights, sort_keys=True),
                "equity_source_contributions": json.dumps(components, sort_keys=True),
                "cap6_equity_symbols": json.dumps(six["selected_equity"]),
                "cap8_equity_symbols": json.dumps(eight["selected_equity"]),
                "v3_requested": len(row["raw_v3_target_symbols"]),
                "reversal_requested": len(row["raw_reversal_target_symbols"]),
                "crypto_requested": len(crypto_symbols),
                "overlap": len(overlap),
                "candidate_unique_slots": len(set(ranks["reversal"]) - set(ranks["v3"])),
                "requested_equity_unique": len(
                    set(row["raw_v3_target_symbols"]) | set(row["raw_reversal_target_symbols"])
                ),
                "requested_total_unique": len(
                    set(row["raw_v3_target_symbols"]) | set(row["raw_reversal_target_symbols"])
                )
                + len(crypto_symbols),
                **{
                    f"cap{cap}_{source}_{field}": result[field][source]
                    for cap, result in ((6, six), (8, eight))
                    for field in ("accepted", "denials")
                    for source in ("v3", "reversal", "crypto")
                },
                "cap6_slots": six["slots"],
                "cap8_slots": eight["slots"],
                "intended_v3": 0.4125,
                "intended_reversal": 0.1375,
                "intended_crypto": 0.05 if crypto_symbols else 0.0,
                "other_constraints": "not_modeled",
            }
        )
    with (HERE / "SLEEVE_CAPACITY_COMPARISON.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)

    def mean(key: str) -> float:
        return sum(float(row[key]) for row in output) / len(output)

    summary = {
        "scope": "TARGET_DEMAND_ONLY_NOT_PHYSICAL_REPLAY",
        "decision_count": len(output),
        "crypto_reference": "isolated corrected causal crypto; target holdings are not shared-account endogenous",
        "cap8_extra_equity_selection_events": cap8_extra,
        "cap6": {
            source: {field: mean(f"cap6_{source}_{field}") for field in ("accepted", "denials")}
            for source in ("v3", "reversal", "crypto")
        },
        "cap8": {
            source: {field: mean(f"cap8_{source}_{field}") for field in ("accepted", "denials")}
            for source in ("v3", "reversal", "crypto")
        },
        "mean_crypto_requested_slots": mean("crypto_requested"),
        "mean_count_unadmitted_target_exposure_diagnostic": {
            str(cap): {
                source: mean(f"intended_{source}") - mean(f"cap{cap}_{source}_accepted")
                for source in ("v3", "reversal", "crypto")
            }
            for cap in (6, 8)
        },
    }
    (HERE / "STRUCTURAL_CAPACITY_COMPARISON.json").write_text(json.dumps(summary, indent=2) + "\n")
    (HERE / "TARGET_OVERLAP_AUDIT.json").write_text(
        json.dumps(
            {
                "decision_count": len(output),
                "overlap_decisions": overlap_decisions,
                "mean_overlapping_names": overlap_total / len(output),
                "slot_savings_from_equity_overlap": overlap_total,
                "cross_market_symbol_overlap": 0,
                "mean_candidate_unique_slot_demand": mean("reversal_requested") - overlap_total / len(output),
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
