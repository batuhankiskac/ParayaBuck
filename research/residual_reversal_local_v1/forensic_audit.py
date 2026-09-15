"""Read-only forensic audit of the frozen residual-reversal campaign.

This module intentionally does not import or invoke ``PortfolioBacktestEngine``
or ``run_campaign.py``.  It reads the immutable campaign artifacts, the
persisted PIT universe and lifetime table, and performs one pure risk-overlay
probe with synthetic targets.  No market return, P&L, Sharpe, drawdown, or
alternative allocation is generated here.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
AUDIT_START_HEAD = "50938a3ac8b899a7528158bc0019808646a196e5"
VALIDATION_START = datetime(2022, 1, 3, tzinfo=UTC)
FULL_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
DEVELOPMENT_START = datetime(2017, 1, 3, tzinfo=UTC)
DEVELOPMENT_END = datetime(2021, 12, 31, 23, 59, 59, tzinfo=UTC)
RUN_NAMES = ("v3_1x", "reversal_1x", "treatment_1x")


def _timestamp(raw: object) -> datetime:
    value = str(raw).replace("Z", "+00:00")
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _load_json(name: str) -> Any:
    return json.loads((CAMPAIGN_DIR / name).read_text(encoding="utf-8"))


def _git_output(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _parse_year_counts(timestamps: list[datetime]) -> dict[str, int]:
    counts = Counter(timestamp.year for timestamp in timestamps)
    return {str(year): counts[year] for year in sorted(counts)}


def _weekly_schedule(curve: list[list[object]], start: datetime) -> list[datetime]:
    """Reconstruct the engine's first-bar-per-ISO-week opportunities."""
    schedule: list[datetime] = []
    previous: datetime | None = None
    for row in curve:
        timestamp = _timestamp(row[0])
        current_week = timestamp.isocalendar()[:2]
        if (previous is None or previous.isocalendar()[:2] != current_week) and timestamp >= start:
            schedule.append(timestamp)
        previous = timestamp
    return schedule


def _period_record(
    run: dict[str, Any],
    schedule: list[datetime],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    scheduled = [timestamp for timestamp in schedule if start <= timestamp <= end]
    decisions = [
        _timestamp(row["decision_timestamp"])
        for row in run["rebalances"]
        if start <= _timestamp(row["decision_timestamp"]) <= end
    ]
    fills = [
        _timestamp(row["timestamp"])
        for row in run["fills"]
        if start <= _timestamp(row["timestamp"]) <= end
    ]
    return {
        "scheduled_weeks": len(scheduled),
        "recorded_decisions": len(decisions),
        "fills": len(fills),
        "last_decision": decisions[-1].isoformat() if decisions else None,
        "last_fill": fills[-1].isoformat() if fills else None,
        "coverage_pct": (100.0 * len(decisions) / len(scheduled)) if scheduled else None,
    }


def _run_coverage(
    name: str,
    run: dict[str, Any],
    schedule: list[datetime],
) -> dict[str, Any]:
    decisions = [_timestamp(row["decision_timestamp"]) for row in run["rebalances"]]
    fills = [_timestamp(row["timestamp"]) for row in run["fills"]]
    annual = []
    scheduled_by_year = Counter(timestamp.year for timestamp in schedule)
    decisions_by_year = Counter(timestamp.year for timestamp in decisions)
    fills_by_year = Counter(timestamp.year for timestamp in fills)
    for year in sorted(scheduled_by_year):
        scheduled = scheduled_by_year[year]
        annual.append(
            {
                "strategy": name.removesuffix("_1x"),
                "year": year,
                "scheduled_weeks": scheduled,
                "recorded_decisions": decisions_by_year[year],
                "fills": fills_by_year[year],
                "coverage_pct": 100.0 * decisions_by_year[year] / scheduled if scheduled else None,
            }
        )
    return {
        "strategy": name.removesuffix("_1x"),
        "first_rebalance": decisions[0].isoformat() if decisions else None,
        "last_rebalance": decisions[-1].isoformat() if decisions else None,
        "first_fill": fills[0].isoformat() if fills else None,
        "last_fill": fills[-1].isoformat() if fills else None,
        "rebalance_count": len(decisions),
        "fill_count": len(fills),
        "rebalance_by_year": _parse_year_counts(decisions),
        "fill_by_year": _parse_year_counts(fills),
        "annual": annual,
        "development": _period_record(run, schedule, DEVELOPMENT_START, DEVELOPMENT_END),
        "validation": _period_record(run, schedule, VALIDATION_START, FULL_END),
    }


def _load_lifetimes(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            str(row["canonical_symbol"]).strip().upper(): dict(row)
            for row in csv.DictReader(handle)
            if row.get("canonical_symbol")
        }


def _lifecycle_findings(
    audit: dict[str, Any],
    coverage: dict[str, dict[str, Any]],
    lifetimes: dict[str, dict[str, str]],
    schedule: list[datetime],
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for name in RUN_NAMES:
        run = audit[name]
        last_decision = _timestamp(run["rebalances"][-1]["decision_timestamp"])
        next_opportunity = next((timestamp for timestamp in schedule if timestamp > last_decision), None)
        last_target = [symbol for symbol, _weight in run["rebalances"][-1]["target_weights"]]
        stale = []
        for symbol in last_target:
            lifetime = lifetimes.get(symbol.upper())
            if not lifetime or lifetime.get("current_status") != "inactive":
                continue
            last_bar = _timestamp(lifetime["last_observed_bar"])
            first_missing_opportunity = next((timestamp for timestamp in schedule if timestamp > last_bar), None)
            if first_missing_opportunity is not None:
                stale.append(
                    {
                        "symbol": symbol,
                        "last_available_bar": last_bar.isoformat(),
                        "first_missing_opportunity": first_missing_opportunity.isoformat(),
                        "entity_valid_to": lifetime.get("valid_to"),
                        "entity_status": lifetime.get("current_status"),
                        "terminal_event_class": lifetime.get("terminal_event_class"),
                        "last_target_weight": dict(run["rebalances"][-1]["target_weights"])[symbol],
                        "missing_at_first_permanent_opportunity": first_missing_opportunity == next_opportunity,
                    }
                )
        findings.append(
            {
                "strategy": name.removesuffix("_1x"),
                "last_decision": last_decision.isoformat(),
                "first_permanent_skipped_opportunity": next_opportunity.isoformat() if next_opportunity else None,
                "last_target_symbols": last_target,
                "stale_managed_symbols": stale,
                "terminal_events_in_artifact": run["terminal_events"],
                "explanation": (
                    "The final-bar liquidation events identify the last available bar, "
                    "but they are emitted only after the entire curve has been processed."
                ),
            }
        )
    return {
        "engine_terminal_map_assignment_present": False,
        "engine_terminal_map_read_default": True,
        "runner_lifetime_metadata_used_for_hash_only": True,
        "findings": findings,
    }


def _synthetic_overlay_probe() -> dict[str, Any]:
    """Show the position-cap outcome without any bars or return calculation."""
    src_path = str(ROOT / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    from parayabuck.config import load_config
    from parayabuck.core.models import AccountSnapshot, Market
    from parayabuck.execution.portfolio import PortfolioRiskOverlay, portfolio_risk_limits_from_config
    from parayabuck.portfolio.base import PortfolioContext
    from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
    from research.residual_reversal_local_v1.candidate import compose_shared_treatment_target

    timestamp = datetime(2024, 1, 2, tzinfo=UTC)
    v3_raw = [(f"V{i}", 0.55 / 8.0) for i in range(8)]
    reversal_raw = [(f"R{i}", 0.55 / 6.0) for i in range(6)]
    v3_target = TargetPortfolio(
        "synthetic-v3",
        "audit",
        timestamp,
        tuple(TargetWeight(symbol, weight, "v3", rank=index + 1) for index, (symbol, weight) in enumerate(v3_raw)),
        0.45,
    )
    reversal_target = TargetPortfolio(
        "synthetic-reversal",
        "audit",
        timestamp,
        tuple(
            TargetWeight(symbol, weight, "reversal", rank=index + 1)
            for index, (symbol, weight) in enumerate(reversal_raw)
        ),
        0.45,
    )
    context = PortfolioContext(
        decision_timestamp=timestamp,
        market=Market.EQUITY,
        timeframe="1d",
        history={},
        universe=None,
        universe_version="",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )
    merged = compose_shared_treatment_target(context, v3_target, reversal_target)
    config = load_config(ROOT / "config/settings.yaml")
    overlay = PortfolioRiskOverlay(portfolio_risk_limits_from_config(config.risk))
    account = AccountSnapshot(100_000.0, 100_000.0, 100_000.0, 0.0)
    decision = overlay.apply(
        merged,
        account,
        market=Market.EQUITY,
        approved_symbols=tuple(symbol for symbol, _weight in [*v3_raw, *reversal_raw]),
        maximum_allocation=0.55,
        mode="backtest",
    )
    kept = [(weight.symbol, weight.weight) for weight in decision.target.weights]
    v3_exposure = sum(weight for symbol, weight in kept if symbol.startswith("V"))
    reversal_exposure = sum(weight for symbol, weight in kept if symbol.startswith("R"))
    return {
        "fixture": "8 V3 names at 0.55/8 and 6 reversal names at 0.55/6",
        "pre_cap_v3_names": [symbol for symbol, _weight in v3_raw],
        "pre_cap_reversal_names": [symbol for symbol, _weight in reversal_raw],
        "overlap": [],
        "merged_names": [weight.symbol for weight in merged.weights],
        "merged_count": len(merged.weights),
        "merged_ranks": sorted({weight.rank for weight in merged.weights}),
        "post_cap_names": [symbol for symbol, _weight in kept],
        "post_cap_weights": kept,
        "effective_v3_exposure": v3_exposure,
        "effective_reversal_exposure": reversal_exposure,
        "unused_intended_sleeve": 0.55 - sum(weight for _symbol, weight in kept),
        "overlay_reasons": list(decision.reasons),
        "proof": (
            "The composed targets all have rank=0, so the production overlay's "
            "unranked branch sorts by target weight and keeps the six larger V3 weights."
        ),
    }


def _treatment_attribution(audit: dict[str, Any]) -> dict[str, Any]:
    rows = audit["treatment_1x"]["rebalances"]
    post_cap_counts = Counter(len(row["target_weights"]) for row in rows)
    reasons = Counter(reason for row in rows for reason in row["risk_overlay_reasons"])
    invested = [sum(weight for _symbol, weight in row["target_weights"]) for row in rows]
    examples = [rows[0]]
    small_weight_row = next(
        (row for row in rows if any(weight <= 0.025 + 1e-12 for _symbol, weight in row["target_weights"])),
        None,
    )
    if small_weight_row is not None and small_weight_row not in examples:
        examples.append(small_weight_row)
    if rows[-1] not in examples:
        examples.append(rows[-1])
    return {
        "historical_raw_sleeve_attribution_persisted": False,
        "historical_raw_attribution_reason": (
            "_rebalance_dict persists only merged target weights and overlay results; "
            "the raw V3 target, raw reversal target, overlap, source ranks and pre-cap "
            "merged set are not serialized."
        ),
        "post_cap_target_count_distribution": {str(key): value for key, value in sorted(post_cap_counts.items())},
        "position_count_limit_decisions": reasons["position_count_limit"],
        "overlay_reason_counts": dict(sorted(reasons.items())),
        "post_cap_invested_exposure": {
            "minimum": min(invested),
            "maximum": max(invested),
            "mean": sum(invested) / len(invested),
            "mean_unused_vs_0.55": 0.55 - sum(invested) / len(invested),
        },
        "historical_examples": [
            {
                "decision": row["decision_timestamp"],
                "post_cap_names": [symbol for symbol, _weight in row["target_weights"]],
                "post_cap_invested": sum(weight for _symbol, weight in row["target_weights"]),
                "overlay_reasons": row["risk_overlay_reasons"],
                "target_weights": row["target_weights"],
            }
            for row in examples
        ],
        "synthetic_probe": _synthetic_overlay_probe(),
    }


def _observation_set_audit(
    universe: dict[str, Any],
    runner_source: str,
    universe_source: str,
    product_source: str,
) -> dict[str, Any]:
    snapshots = universe["snapshots"]
    names = sorted({snapshot["observation_strategy_name"] for snapshot in snapshots})
    versions = sorted({snapshot["observation_strategy_version"] for snapshot in snapshots})
    return {
        "scanner_eligible_pool": {
            "method": universe["methodology"],
            "average_pool_size": universe["average_pool_size"],
            "target_shortlist": 300,
            "conforms_to_broad_pool_contract": True,
        },
        "v3_observation_set": {
            "method": "build_observation_set(V3 alpha, scanner-selected pool, pinned references)",
            "strategy_names": names,
            "strategy_versions": versions,
            "maximum_stream_symbols": sorted({snapshot["maximum_stream_symbols"] for snapshot in snapshots}),
            "conforms_to_broad_pool_contract": True,
        },
        "reversal_observation_set_actual": {
            "method": "the same universe_obj and snapshot.observation_set passed to run_one(reversal)",
            "own_alpha_ranker_used": False,
            "conforms_to_broad_pool_contract": False,
            "violation": "HIDDEN_V3_ALPHA_PREFILTER_CONFIRMED",
        },
        "treatment_observation_set_actual": {
            "method": "the same V3 observation set; both component decisions receive that context",
            "own_alpha_ranker_used": False,
            "predeclared_union_policy_used": False,
            "conforms_to_broad_pool_contract": False,
            "violation": "shared V3 observation contamination",
        },
        "source_proof": {
            "runner_builds_one_universe": "universe_obj = build_product_universe" in runner_source,
            "runner_passes_same_universe_to_all_runs": runner_source.count("run_one(name, cost, config, engine_bars, numeric_history, universe_obj, actions)") == 1,
            "product_builder_constructs_configured_observation_strategy": "observation_strategy = strategy" in product_source,
            "product_builder_passes_configured_observation_strategy": "observation_strategy=observation_strategy" in product_source,
            "universe_builder_ranks_observation_set": "ranked_observation = build_observation_set" in universe_source,
        },
    }


def _verifier_audit(verifier_source: str) -> dict[str, Any]:
    return {
        "checks_observed": {
            "weekly_uniqueness_only": "len(weekly) == len(set(weekly))" in verifier_source,
            "validation_boundary_only": "start >= _timestamp(manifest[\"windows\"][\"validation_start\"])" in verifier_source,
        },
        "important_invariants_not_checked": [
            {
                "invariant": "minimum or expected weekly opportunity count",
                "why_missing": "No schedule is reconstructed and no expected count is compared with recorded decisions.",
            },
            {
                "invariant": "decision continuity through validation",
                "why_missing": "The verifier only checks uniqueness of recorded weeks, so an empty multi-year tail passes.",
            },
            {
                "invariant": "last decision date near the data end",
                "why_missing": "No recency or maximum-gap assertion exists.",
            },
            {
                "invariant": "nonzero validation rebalance activity",
                "why_missing": "Validation boundary checks reported dates, not decision or fill counts.",
            },
            {
                "invariant": "observation completeness skip rate",
                "why_missing": "Skipped decisions are not persisted as attempts or counted.",
            },
            {
                "invariant": "terminal lifecycle correctness",
                "why_missing": "The verifier hashes the lifetime file but never checks engine wiring or inactive managed pins.",
            },
            {
                "invariant": "realized sleeve attribution after position truncation",
                "why_missing": "Only merged target caps and aggregate exposure are checked; source ownership is absent.",
            },
            {
                "invariant": "strategy-specific observation-set identity",
                "why_missing": "No assertion compares the run strategy with the persisted observation_strategy_name.",
            },
        ],
        "why_original_verifier_passed": (
            "The artifact identities, signal unit probe, unique-week condition, next-bar fill arithmetic, "
            "target caps, NAV identity and validation date bounds all passed. None of those checks requires "
            "a positive number of validation decisions, a live terminal map, sleeve attribution, or a "
            "strategy-owned observation set."
        ),
    }


def build_audit() -> dict[str, Any]:
    manifest = _load_json("run_manifest.json")
    receipt = _load_json("DATA_RECEIPT.json")
    audit = _load_json("execution_audit.json")
    universe = _load_json("universe_snapshot.json")
    v3 = _load_json("v3_control.json")
    reversal = _load_json("standalone_reversal.json")
    treatment = _load_json("treatment.json")
    lifetime_path = Path(receipt["lifetime_table"])
    lifetimes = _load_lifetimes(lifetime_path)
    curve = audit["v3_1x"]["curve"]
    first_strategy_ready = min(_timestamp(row["decision_timestamp"]) for row in audit["v3_1x"]["rebalances"])
    schedule = _weekly_schedule(curve, first_strategy_ready)
    coverage = {name.removesuffix("_1x"): _run_coverage(name, audit[name], schedule) for name in RUN_NAMES}
    runner_source = (CAMPAIGN_DIR / "run_campaign.py").read_text(encoding="utf-8")
    candidate_source = (CAMPAIGN_DIR / "candidate.py").read_text(encoding="utf-8")
    engine_source = (ROOT / "src/parayabuck/backtest/portfolio_engine.py").read_text(encoding="utf-8")
    overlay_source = (ROOT / "src/parayabuck/execution/portfolio.py").read_text(encoding="utf-8")
    universe_source = (ROOT / "src/parayabuck/backtest/universe.py").read_text(encoding="utf-8")
    product_source = (ROOT / "src/parayabuck/backtest/product.py").read_text(encoding="utf-8")
    verifier_source = (CAMPAIGN_DIR / "verify_results.py").read_text(encoding="utf-8")
    stale_source_proof = {
        "engine_reads_optional_terminal_map": "terminal_map = getattr(self, \"terminal_map\", {}) or {}" in engine_source,
        "engine_assigns_terminal_map": bool(re.search(r"self\.terminal_map\s*=", engine_source)),
        "runner_mentions_terminal_map": "terminal_map" in runner_source,
        "runner_hashes_lifetime_file": "lifetimes_sha = _hash_file(args.lifetimes)" in runner_source,
        "merged_targets_set_rank_zero": "rank=0" in candidate_source,
        "overlay_has_unranked_weight_sort": "priority = sorted(desired, key=lambda symbol: (-desired[symbol], symbol))" in overlay_source,
    }
    full_curve_start = _timestamp(curve[0][0])
    full_curve_end = _timestamp(curve[-1][0])
    return {
        "forensic_verdict": "EXPERIMENT_INVALID_IMPLEMENTATION",
        "economic_rerun_performed": False,
        "identity": {
            "audit_start_head": AUDIT_START_HEAD,
            "current_head_at_audit": _git_output("rev-parse", "HEAD"),
            "branch": _git_output("branch", "--show-current"),
            "origin_branch_head_at_audit": _git_output("rev-parse", "origin/research/residual-reversal-local-v1"),
            "main_sha": _git_output("rev-parse", "main"),
            "campaign_artifact_commit": manifest.get("campaign_artifact_commit"),
            "campaign": manifest["campaign"],
            "original_verdict": manifest["verdict"],
        },
        "finding_1_validation_coverage": {
            "strategy_ready_schedule_start": first_strategy_ready.isoformat(),
            "schedule_basis": "first completed bar of each ISO week on the persisted full curve, after common 274-bar warmup",
            "full_curve_start": full_curve_start.isoformat(),
            "full_curve_end": full_curve_end.isoformat(),
            "validation_start": VALIDATION_START.isoformat(),
            "normal_scheduled_decision_after_validation_start": any(
                _timestamp(row["decision_timestamp"]) >= VALIDATION_START
                for name in RUN_NAMES
                for row in audit[name]["rebalances"]
            ),
            "coverage": coverage,
            "reported_validation_metrics": {
                "v3": v3["costs"]["1"]["validation"],
                "reversal": reversal["costs"]["1"]["validation"],
                "treatment": treatment["costs"]["1"]["validation"],
            },
            "interpretation": {
                "active_strategy_return": "No active strategy decisions or decision-driven fills occurred in validation.",
                "passive_mark_to_market": "The existing daily curve continued marking positions held from the last development decision.",
                "dividends": "The engine applied dividends to held positions; the artifacts persist full-run dividend totals but not a validation-period dividend ledger.",
                "terminal_liquidation": "liquidate_at_end mutated the final curve point after the last bar; this is not a validation-period strategy decision.",
                "admissibility": "A no-decision interval cannot be described as valid out-of-sample strategy validation.",
            },
        },
        "finding_2_terminal_lifecycle": {
            "source_proof": stale_source_proof,
            "reconstruction": _lifecycle_findings(audit, coverage, lifetimes, schedule),
        },
        "finding_3_treatment_cap": _treatment_attribution(audit),
        "finding_4_observation_sets": _observation_set_audit(universe, runner_source, universe_source, product_source),
        "finding_5_verifier_gaps": _verifier_audit(verifier_source),
        "repair_specification": [
            "Wire the hashed v5 lifetime table into the engine's terminal/lifecycle map and persist an explicit terminal close/removal event before a managed inactive symbol can block completeness.",
            "Add fail-fast coverage assertions for expected weekly opportunities, minimum coverage, maximum unexplained gaps, and real validation decisions/fills; classify passive carry separately from active validation.",
            "Persist raw V3 and reversal target names, ranks, overlap, merged pre-cap targets, post-cap survivors, sleeve exposures and unused intended sleeve on every treatment decision.",
            "Make the shared six-position policy sleeve-aware and deterministic, preserving source strategy identity/rank or another predeclared account-level allocation rule so 75/25 cannot be silently converted into target-weight selection.",
            "Build strategy-specific observation sets: V3 ranked by V3, reversal ranked by reversal, and treatment using a predeclared combined/union policy.",
            "Extend independent verification to enforce lifecycle wiring, observation identity, decision continuity, coverage, and sleeve attribution before any corrected replay is accepted.",
            "Replay the same frozen candidate only after these implementation corrections; do not retune parameters or modify the frozen gates.",
        ],
        "preserved_original_evidence": [
            "FROZEN_PROTOCOL.md",
            "DATA_RECEIPT.json",
            "run_manifest.json",
            "v3_control.json",
            "standalone_reversal.json",
            "treatment.json",
            "execution_audit.json",
            "independent_verification.json",
            "FINAL_REPORT.md",
            "decision.json",
            "gate_table.json",
        ],
    }


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _coverage_table(audit: dict[str, Any]) -> str:
    rows = [
        ("V3", "development", audit["v3"]["development"]),
        ("V3", "validation", audit["v3"]["validation"]),
        ("Reversal", "development", audit["reversal"]["development"]),
        ("Reversal", "validation", audit["reversal"]["validation"]),
        ("Treatment", "development", audit["treatment"]["development"]),
        ("Treatment", "validation", audit["treatment"]["validation"]),
    ]
    lines = [
        "| Strategy | Period | Scheduled weeks | Recorded decisions | Fills | Last decision | Last fill | Coverage % |",
        "| --- | --- | ---: | ---: | ---: | --- | --- | ---: |",
    ]
    for strategy, period, row in rows:
        lines.append(
            f"| {strategy} | {period} | {row['scheduled_weeks']} | {row['recorded_decisions']} | {row['fills']} | "
            f"{row['last_decision'] or 'n/a'} | {row['last_fill'] or 'n/a'} | {_fmt(row['coverage_pct'])} |"
        )
    return "\n".join(lines)


def _annual_table(audit: dict[str, Any]) -> str:
    lines = [
        "| Strategy | Year | Scheduled weeks | Recorded decisions | Fills | Coverage % |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in ("v3", "reversal", "treatment"):
        for row in audit[name]["annual"]:
            lines.append(
                f"| {row['strategy']} | {row['year']} | {row['scheduled_weeks']} | {row['recorded_decisions']} | "
                f"{row['fills']} | {_fmt(row['coverage_pct'])} |"
            )
    return "\n".join(lines)


def render_markdown(audit: dict[str, Any], *, artifact_commit: str = "pending") -> str:
    identity = audit["identity"]
    coverage = audit["finding_1_validation_coverage"]["coverage"]
    finding_2 = audit["finding_2_terminal_lifecycle"]
    finding_3 = audit["finding_3_treatment_cap"]
    finding_5 = audit["finding_5_verifier_gaps"]
    stale_rows = [item for item in finding_2["reconstruction"]["findings"]]
    stale_table = [
        "| Strategy | First permanent skipped opportunity | Symbol first missing opportunity | Symbol | Last available bar | Entity status | Terminal class |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in stale_rows:
        for stale in row["stale_managed_symbols"]:
            stale_table.append(
                f"| {row['strategy']} | {row['first_permanent_skipped_opportunity']} | "
                f"{stale['first_missing_opportunity']} | {stale['symbol']} | {stale['last_available_bar']} | "
                f"{stale['entity_status']} | {stale['terminal_event_class']} |"
            )
    if len(stale_table) == 2:
        stale_table.append("| n/a | n/a | n/a | n/a | n/a | n/a | n/a |")
    treatment_examples = [
        "| Decision | Post-cap names | Post-cap invested | Overlay reasons |",
        "| --- | --- | ---: | --- |",
    ]
    for row in finding_3["historical_examples"]:
        treatment_examples.append(
            f"| {row['decision']} | {', '.join(row['post_cap_names'])} | {_fmt(row['post_cap_invested'], 6)} | "
            f"{', '.join(row['overlay_reasons'])} |"
        )
    synthetic = finding_3["synthetic_probe"]
    verifier_gap_lines = "\n".join(
        f"- `{item['invariant']}` — {item['why_missing']}" for item in finding_5["important_invariants_not_checked"]
    )
    return f"""# Forensic audit: equity short-term residual reversal v1

## Forensic verdict

`{audit['forensic_verdict']}`

The original `RESEARCH_REJECT` is preserved as an immutable historical artifact, but it is **not admissible as candidate-rejection evidence** because the frozen experiment was not implemented/replayed with valid decision coverage, lifecycle handling, sleeve attribution, or strategy-specific observation sets.

## Branch / artifact identity

- Audit starting HEAD recorded before inspection: `{identity['audit_start_head']}`.
- Branch: `{identity['branch']}`; origin matched the starting HEAD at audit start: `{identity['origin_branch_head_at_audit']}`.
- `main`: `{identity['main_sha']}`.
- Existing campaign artifact commit: `{identity['campaign_artifact_commit']}`.
- Forensic artifact commit: `{artifact_commit}`.
- Economic rerun performed: **no**.

Original campaign files were not overwritten. The audit reads the persisted run artifacts, the persisted universe snapshot, and the v5 lifetime table. It does not call `run_campaign.py`, `PortfolioBacktestEngine`, or any return-generating path.

## Finding 1 — validation decision coverage

The three 1x runs contain no recorded normal scheduled decision at or after `{VALIDATION_START.isoformat()}`. Their nonzero validation returns are therefore passive carry/mark-to-market of positions established in development, plus dividends and the engine's end-of-run liquidation adjustment; they are not active out-of-sample strategy validation.

| Strategy | First rebalance | Last rebalance | First fill | Last decision-driven fill | Rebalances | Fills |
| --- | --- | --- | --- | --- | ---: | ---: |
| V3 | {coverage['v3']['first_rebalance']} | {coverage['v3']['last_rebalance']} | {coverage['v3']['first_fill']} | {coverage['v3']['last_fill']} | {coverage['v3']['rebalance_count']} | {coverage['v3']['fill_count']} |
| Reversal | {coverage['reversal']['first_rebalance']} | {coverage['reversal']['last_rebalance']} | {coverage['reversal']['first_fill']} | {coverage['reversal']['last_fill']} | {coverage['reversal']['rebalance_count']} | {coverage['reversal']['fill_count']} |
| Treatment | {coverage['treatment']['first_rebalance']} | {coverage['treatment']['last_rebalance']} | {coverage['treatment']['first_fill']} | {coverage['treatment']['last_fill']} | {coverage['treatment']['rebalance_count']} | {coverage['treatment']['fill_count']} |

The original period calculator selected curve points in the validation date range and computed returns from those points; it did not require a validation decision or fill. Full-run dividend totals are retained in the original result artifacts, but no validation-period dividend ledger was persisted. Terminal events for inactive holdings are timestamped at their last available bar even though the actual liquidation is performed only after the full curve has been processed.

## Finding 2 — terminal lifecycle / completeness gate

Confirmed defect: the runner hashes `equity_entity_lifetimes_full_v5.csv` but does not pass a terminal/lifecycle map into `PortfolioBacktestEngine`. The engine reads an optional `terminal_map` with a default empty mapping; there is no `self.terminal_map = ...` assignment in the engine or runner.

The engine then reserves managed positions in `_effective_observation_symbols`. With the map absent, an inactive managed symbol remains active for the completeness test. At the next weekly opportunity, its missing current bar causes `_decide` to return before strategy decision and target application.

{chr(10).join(stale_table)}

The wedge is visible in existing evidence:

- V3 and treatment last decide on `2018-12-17`; the first permanently skipped weekly opportunity is `2018-12-24`, with `ESRX` held and last observed on `2018-12-20`. `DVMT` then reaches its last bar on `2018-12-27`.
- Reversal last decides on `2021-12-13`; the first permanently skipped weekly opportunity is `2021-12-20`, with `KSU` held and last observed on `2021-12-13`.
- The earlier isolated skipped week on `2018-10-01` is not the permanent wedge; later decisions resumed. The permanent tails do not.

The `FINAL_BAR` events in `execution_audit.json` are not evidence that lifecycle handling worked. They are the separate `liquidate_at_end` cleanup after the run; for stale symbols they use the old last bar and mutate the final curve value.

## Finding 3 — six-position treatment truncation

Historical raw sleeve attribution is unavailable. `_rebalance_dict` persists only the merged post-overlay target and execution fields; it does not persist raw V3 names, raw reversal names, overlap, source ranks, or pre-cap merged targets. Therefore no historical V3/reversal exposure split is invented here.

Observed treatment evidence: all 97 recorded treatment decisions carry `position_count_limit`; post-cap target counts are `{json.dumps(finding_3['post_cap_target_count_distribution'], sort_keys=True)}`. Post-cap invested exposure ranged from `{_fmt(finding_3['post_cap_invested_exposure']['minimum'], 6)}` to `{_fmt(finding_3['post_cap_invested_exposure']['maximum'], 6)}`, with mean `{_fmt(finding_3['post_cap_invested_exposure']['mean'], 6)}`. This is under the intended 0.55 sleeve before considering the separate risk-halt effects.

{chr(10).join(treatment_examples)}

Pure synthetic target probe, with no market data or returns:

- Pre-cap: 8 V3 names, 6 reversal names, zero overlap, 14 merged names.
- Every merged target has `rank=0`.
- The production overlay keeps six V3 names by larger target weight, drops all reversal names, realizes V3 exposure `{_fmt(synthetic['effective_v3_exposure'], 6)}`, reversal exposure `{_fmt(synthetic['effective_reversal_exposure'], 6)}`, and leaves `{_fmt(synthetic['unused_intended_sleeve'], 6)}` of the intended 0.55 sleeve unused.
- Overlay reason: `{', '.join(synthetic['overlay_reasons'])}`.

Thus the implementation did not prove realization of `0.4125 V3 + 0.1375 reversal`; it made that split vulnerable to systematic position-cap truncation. A historical sleeve-share distribution requires corrected per-decision attribution metadata.

## Finding 4 — observation-set contamination

| Layer | Actual methodology | Own alpha ranks broad eligible pool? | Finding |
| --- | --- | --- | --- |
| Scanner eligible pool | Daily PIT scanner on trailing 60-day metrics, Stage-A shortlist capped at 300 by median dollar volume | n/a — this is the broad pool | Conforms |
| V3 observation set | `build_observation_set` ranked the scanner pool with V3 alpha, plus pinned references, up to 30 symbols | Yes | Conforms |
| Standalone reversal | Reused the same `universe_obj` and V3-ranked `snapshot.observation_set`; reversal scored only visible history | No | `HIDDEN_V3_ALPHA_PREFILTER_CONFIRMED` |
| Treatment | Reused the same V3 observation set for both component decisions | No | Shared V3 observation contamination |

The tested standalone object was therefore approximately “short-term residual reversal conditional on V3 observation selection,” not pure short-term residual reversal over its own broad eligible pool. No corrected return is calculated in this audit.

## Finding 5 — independent verifier gaps

The verifier checked uniqueness of ISO weeks among recorded decisions and checked that the reported validation dates lie inside the frozen boundary. Both checks can pass with zero decisions across several years.

Important invariants not checked:

{verifier_gap_lines}

The independent verifier returned `PASS` because hashes, signal probes, fill arithmetic, caps, NAV identities and date boundaries were internally consistent. Those checks do not establish active validation, lifecycle correctness, sleeve attribution, or strategy-owned observation identity.

## Decision-coverage table

Schedule denominator: first completed bar of each ISO week after the common 274-bar warmup, reconstructed from the existing full curve. No new returns are generated.

{_coverage_table(coverage)}

Annual structural coverage:

{_annual_table(coverage)}

## Treatment attribution

The original artifacts are insufficient for historical pre-risk sleeve attribution. The corrected runner must persist raw component targets and source identity before the shared overlay. The deterministic synthetic proof above is the admissible evidence for the current implementation.

## Economic-result admissibility

The existing `RESEARCH_REJECT` may remain as a historical record of what the original code emitted. It may **not** be used as evidence that the frozen candidate economically failed. The admissible conclusion from this audit is implementation invalidity, not a strategy verdict.

## Minimal repair specification

1. Wire the hashed v5 lifetime metadata into the engine terminal map and emit deterministic lifecycle close/removal events before inactive managed symbols can block completeness.
2. Add fail-fast expected-week, minimum-coverage, maximum-gap and validation-activity assertions; separate passive carry, dividends and terminal cleanup from active validation.
3. Persist raw V3/reversal names, ranks, overlap, merged pre-cap targets, post-cap survivors, sleeve exposures and unused intended sleeve on every treatment decision.
4. Make the six-position policy sleeve-aware and deterministic under the frozen 75/25 allocation; preserve source rank/identity or use another predeclared account-level rule.
5. Build V3-specific, reversal-specific, and predeclared treatment observation sets from the broad eligible pool.
6. Strengthen the independent verifier to enforce all of the above before replay.

The next step is an implementation-corrected replay of the same frozen candidate. No parameter retune, gate change, or new strategy is proposed here.

## Files created

- `FORENSIC_AUDIT.md`
- `FORENSIC_AUDIT.json`
- `forensic_audit.py`
- `test_forensic_invariants.py`

## Commit SHA

Forensic artifact commit: `{artifact_commit}`. The final branch tip is reported in the task handoff.
"""


def write_outputs(audit: dict[str, Any], *, artifact_commit: str = "pending") -> None:
    (CAMPAIGN_DIR / "FORENSIC_AUDIT.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (CAMPAIGN_DIR / "FORENSIC_AUDIT.md").write_text(
        render_markdown(audit, artifact_commit=artifact_commit), encoding="utf-8"
    )


if __name__ == "__main__":
    write_outputs(build_audit())
    print("Forensic audit artifacts written; no economic run performed.")
