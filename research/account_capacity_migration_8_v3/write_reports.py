"""Render the frozen comparison from independently verified local artifacts."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent


def load(name: str) -> Any:
    return json.loads((OUT / name).read_text())


def write(name: str, body: str) -> None:
    (OUT / name).write_text(body.rstrip() + "\n")


def number(value: int | float) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.6f}"


def table(a: dict[str, Any], b: dict[str, Any], metrics: list[tuple[str, str]]) -> str:
    lines = ["| Metric | Cap 6 | Cap 8 | Difference (8−6) |", "|---|---:|---:|---:|"]
    for key, label in metrics:
        left, right = a[key], b[key]
        lines.append(f"| {label} | {number(left)} | {number(right)} | {number(right - left)} |")
    return "\n".join(lines)


def main() -> None:
    verify = load("INDEPENDENT_MIGRATION_VERIFICATION_V3.json")
    if verify["status"] != "PASS":
        raise RuntimeError("Independent verification must pass before writing reports")
    freeze = load("FROZEN_MIGRATION_SPEC_V3.json")
    decision = load("MIGRATION_DECISION_V3.json")
    control = load("CONTROL_CAP6_RESULTS_V3.json")
    treatment = load("TREATMENT_CAP8_RESULTS_V3.json")
    a, b = control["structural"], treatment["structural"]
    econ = load("ECONOMIC_METRICS_V3.json")
    denial = {
        cap: load(f"{prefix}_SLOT_DENIALS_V3.json") for cap, prefix in ((6, "CONTROL_CAP6"), (8, "TREATMENT_CAP8"))
    }
    direction_rows: list[str] = []
    for direction, label in (("CRYPTO_TO_EQUITY", "Crypto → Equity"), ("EQUITY_TO_CRYPTO", "Equity → Crypto")):
        direction_rows.extend(
            [
                f"## {label}",
                "",
                "| Arm | Events | Distinct timestamps | Target shortfall notional |",
                "|---|---:|---:|---:|",
            ]
        )
        for cap in (6, 8):
            rows = [row for row in denial[cap] if row["crowd_out_direction"] == direction]
            direction_rows.append(
                f"| {cap} | {len(rows)} | {len({row['timestamp'] for row in rows})} | {number(sum(float(row['count_shortfall_notional'] or 0) for row in rows))} |"
            )
        direction_rows.append("")
        for cap in (6, 8):
            rows = [row for row in denial[cap] if row["crowd_out_direction"] == direction]
            for row in rows[:3]:
                direction_rows.append(
                    f"- Cap {cap}: `{row['timestamp']}` {row['market']} `{row['symbol']}`, occupied/reserved claims {row['account_slot_claims_before']}, opposite-market claims {row['opposite_market_claims_before']}."
                )
        direction_rows.append("")
    write(
        "CROSS_MARKET_CROWDOUT_V3.md",
        "# Cross-market crowd-out V3\n\n"
        + "\n".join(direction_rows)
        + "\nTarget rejection is a point-in-time decision. No symbol-specific blocking duration or denied-symbol hypothetical P&L is defined by this replay. Count shortfall is requested target notional at those decisions, not inferred unused cash.",
    )
    ops = [
        ("maximum_actual_positions", "Peak actual positions"),
        ("maximum_effective_slot_claims", "Peak occupied/reserved claims"),
        ("peak_working_orders", "Peak working orders"),
        ("orders_per_decision", "Orders per decision"),
        ("orders", "Orders"),
        ("fills", "Fills"),
        ("reconciliation_marks", "Account reconciliation marks"),
        ("reservations_created", "Reservations created"),
        ("reservations_released", "Reservations released"),
        ("pending_conflict_events", "Pending-order conflicts"),
    ]
    write(
        "OPERATIONAL_LOAD_COMPARISON_V3.md",
        "# Operational load V3\n\n"
        + table(a, b, ops)
        + "\n\nBoth arms pass account-wide slot and reservation release gates. Open-order pagination focused tests passed locally; no broker API was called. Production capacity would require a separate parity review.",
    )
    economic_keys = [(key, key.replace("_", " ").title()) for key in freeze["economic_metrics"]]
    write(
        "ECONOMIC_COMPARISON_V3.md",
        "# Economic comparison V3\n\n"
        + table(econ["cap6"], econ["cap8"], economic_keys)
        + "\n\nThe full common physical-clock account period is used. No new development/validation split or performance threshold was introduced. Economics are diagnostic and do not override the structural gate.",
    )
    structural_metrics = [
        ("global_count_denied_target_openings", "Count-denied target openings"),
        ("global_count_denied_order_admissions", "Count-denied order admissions"),
        ("timestamps_at_global_cap", "Timestamps at global cap"),
        ("count_target_notional_shortfall_sum", "Count-specific target notional shortfall sum"),
        ("average_equity_positions_daily", "Average equity positions"),
        ("average_crypto_positions_daily", "Average crypto positions"),
        ("average_positions_daily", "Average total positions"),
        ("maximum_effective_slot_claims", "Peak effective slot claims"),
    ]
    reasons = {cap: Counter(row["reason"] for row in denial[cap]) for cap in (6, 8)}
    reason_lines = ["| Cause | Cap 6 | Cap 8 |", "|---|---:|---:|"]
    for cause in sorted(reasons[6].keys() | reasons[8].keys()):
        reason_lines.append(f"| {cause} | {reasons[6][cause]} | {reasons[8][cause]} |")
    concentration = {cap: load(f"CONCENTRATION_CAP{cap}_V3.json") for cap in (6, 8)}
    concentration_lines = table(
        a,
        b,
        [
            ("average_hhi_daily", "Average total-account HHI (daily last marks)"),
            ("maximum_actual_name_weight", "Maximum trace-valued name weight"),
            ("maximum_name_target_weight", "Maximum target name weight"),
        ],
    )
    concentration_lines += "\n\n" + table(
        concentration[6],
        concentration[8],
        [
            ("equity_hhi_active_day_average", "Equity HHI, active days"),
            ("crypto_hhi_active_day_average", "Crypto HHI, active days"),
        ],
    )
    report = f"""# Final account-capacity migration V3 report

## Final migration decision

`{decision["decision"]}`. Structural gate: **{decision["structural_gate"]}**. Next action: `{decision["next_action"]}`.

## Starting main SHA and physical-clock V2 closure

Starting production main was `{freeze["production_main_before_closure_sha"]}`. The documentation-only closure was committed on main as `{freeze["starting_main_sha"]}` locally and remains unpublished. V2 was classified `PHYSICAL_AVAILABILITY_CLOCK_V2_VALIDATED_WITH_LIMITATIONS` and `MIGRATION_REPLAY_READY`. Its completed branch was removed after preservation by the immutable tag. The obsolete label-clock replay lacks its full event trace, so exact old-clock cross-market claimant flips cannot be recovered.

## Authorities and freeze

The annotated infrastructure tag `{freeze["validated_infrastructure"]["tag"]}` peels to `{freeze["validated_infrastructure"]["peeled_commit"]}`. The corrected causal crypto baseline is `CRYPTO_CANONICAL_BASELINE_CAUSAL_REPAIR_ACCEPTED` with 454 decisions and 406 fills. Authority receipts pass. The pre-outcome freeze commit is `{control["freeze_commit_sha"]}` and the verifier confirmed it predates both outcomes. Imported source files initially matched the tag exactly. Only the constructor guard was extended to allow the frozen cap-8 arm; the same replay and input paths ran both arms.

## Arm definitions, period, and input identity

Control: account `maximum_positions=6`. Treatment: account `maximum_positions=8`. The account starts flat with $100,000 cash, no pending orders, and no broker state. First physical event: `{freeze["period"]["account_first_price_event_utc"]}`; final crypto terminal: `{freeze["period"]["crypto_precommitted_terminal_utc"]}`. Equity V3 and corrected crypto low-variance hysteresis are the only strategies. The frozen config SHA256 is `{freeze["config_sha256"]}`. Direct source hashes and derived PIT identities are in the freeze JSON; both arm traces match them exactly. Physical timing, same-timestamp ordering, final liquidation, costs, gross, sleeve, and per-name rules are unchanged.

## Structural capacity results

{table(a, b, structural_metrics)}

The notional shortfall is summed across distinct decisions; it is not realized idle capital or hypothetical P&L. No unused cash is attributed to the count cap without an observed denial.

## Slot denial attribution

{"\n".join(reason_lines)}

Daily-loss halts take precedence when risk reasons overlap with position count. Pending conflicts are counted separately. Liquidity, price, signal eligibility, cash, and gross/sleeve/per-name causes are not inferred when the trace lacks a deterministic rejection reason.

## Cross-market crowd-out

See `CROSS_MARKET_CROWDOUT_V3.md` for both directions, affected symbols/timestamps, slot state, and target shortfall. Event-level rejections have no defined lasting duration.

## Structural migration gate

Cap 6 observed {a["global_count_denied_target_openings"]} count-denied target openings and {a["global_count_denied_order_admissions"]} count-denied order admissions. Cap 8 observed {b["global_count_denied_target_openings"]} and {b["global_count_denied_order_admissions"]}. The count-specific total decreased from {verify["count_denials"]["6"]} to {verify["count_denials"]["8"]}. This is an architecture result, independent of return ranking.

## Exposure and risk invariants

{table(a, b, [("maximum_target_gross", "Maximum target gross"), ("maximum_realized_gross", "Maximum observed gross"), ("average_realized_gross_daily", "Average realized gross"), ("average_cash_daily", "Average cash")])}

Both arms pass cash, inventory, slot, gross target, sleeve, per-name, reservation, corporate-action, causality, and deterministic replay gates. Realized gross can drift between decisions; the unchanged 0.60 target limit remains enforced.

## Concentration comparison

{concentration_lines}

Per-name target caps are unchanged (equity 0.10, crypto 0.05). The event trace omits marks for symbols before they become holdings, so intraday fill-time HHI is a trace-price proxy; daily last-mark sleeve HHI is reported separately. Lower HHI is not treated as alpha evidence.

## Operational load

{table(a, b, ops)}

The treatment remains within eight occupied/reserved claims. Focused pagination and shared-slot tests passed; no broker API call was made.

## Economic comparison

{table(econ["cap6"], econ["cap8"], economic_keys)}

One frozen full-period look was used. No performance threshold, cap search, strategy retuning, or new partition was introduced. Costs and risk regime must be read with the structural changes; economics do not rescue a failed structural or correctness gate.

## Determinism and independent verification

Both arms were run twice. Decision, order, fill, reservation, position, cash, NAV, complete event, and final-state hashes matched within each arm. The independent verifier read compressed raw traces and recomputed event/component hashes, ledger cash, inventory count, slot claims, target limits, order and fill counts, costs, turnover, direct count denials, and economic metrics. Its status is `{verify["status"]}`. Full NAV valuation at fill-time cannot be reconstructed independently from a trace that omits non-held source marks; validated replay internal NAV reconciliation remains a separate gate.

## Decision rationale and governance

The result follows the predeclared structural count gate, not historical performance chasing. Previously `ALPHA_VALID_PORTFOLIO_REJECTED` families may receive a separate rerun-eligibility review if migration is accepted; none were rerun here. The old crypto giveback audit was not used and needs a separate causal-baseline refresh.

## Production state and publication

Production `maximum_positions` remains 6. Equity V3, crypto strategy and rebalance band, PAPER/LIVE modes, and production code/config remain unchanged. A production parity review is the next action if accepted. Local `refs/heads/main` at `{freeze["starting_main_sha"]}` and local `refs/heads/research/account-capacity-migration-8-v3` require exact publication approval before push; no new migration tag was created. The final branch SHA is supplied with the completed local commit in the delivery message.
"""
    write("FINAL_MIGRATION_REPORT_V3.md", report)


if __name__ == "__main__":
    main()
