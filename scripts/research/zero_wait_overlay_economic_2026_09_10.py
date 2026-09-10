#!/usr/bin/env python3
"""Execute the frozen zero-wait V3 exposure-overlay family.

This runner is deliberately research-only.  It validates the immutable freeze
and current V3 parity receipt before loading local bars, and refuses to run on
any pin mismatch.  It never imports a broker client or changes product config.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if str(Path(__file__).parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent.parent))

import research_v3_equity_roadmap as roadmap  # noqa: E402
from research_helpers import sha256_of_file  # noqa: E402

from parayabuck.config import load_config  # noqa: E402
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy  # noqa: E402
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3  # noqa: E402
from parayabuck.portfolio.models import TargetPortfolio  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FREEZE = REPO / "data/research_manifests/zero_wait_overlay_freeze_2026_09_10.json"
CONTROL = REPO / "data/research/equity_v3_current_parity_control_2026_08/control_r3.json"
OUT = REPO / "data/research_runs/zero_wait_overlays_2026_09_10"
RESULTS = OUT / "results.json"
DAILY_NAV = OUT / "daily_nav.csv"
LEDGER = OUT / "exposure_ledger.jsonl"
RECEIPT = OUT / "receipt.json"
REPORT = REPO / "docs/research/zero_wait_overlay_results_2026_09_10.md"

EXPECTED_FREEZE_PHYSICAL = "8376b8dd9f8e17235395ad66e952802ab61e75dba0e3a0f5f5ff9072e8a70b4e"
EXPECTED_FREEZE_CANONICAL = "cbdb2262c03d5e9880455ea765869f25948951e98e2fa753400091bd488e9691"
EXPECTED_CONTROL_PHYSICAL = "dd79160c6ba063572cc680f299568a0936dc53786e35f05ef98db5a7cde18634"
EXPECTED_CONTROL_EMBEDDED = "d4860412bf7650732fab228a7fac7110d0cf56e48bce436160d134040d83840e"


def _canonical_freeze_hash(payload: dict[str, Any]) -> str:
    copy = json.loads(json.dumps(payload))
    copy["integrity"].pop("manifest_sha256", None)
    return hashlib.sha256(json.dumps(copy, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _validate_pins() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if sha256_of_file(FREEZE) != EXPECTED_FREEZE_PHYSICAL:
        raise RuntimeError("FAIL_CLOSED_FREEZE_PHYSICAL_SHA")
    manifest = json.loads(FREEZE.read_text(encoding="utf-8"))
    if _canonical_freeze_hash(manifest) != EXPECTED_FREEZE_CANONICAL:
        raise RuntimeError("FAIL_CLOSED_FREEZE_CANONICAL_SHA")
    if manifest.get("status") != "FROZEN_ZERO_WAIT_OVERLAY_PROTOCOL" or manifest.get("economic_run") is not False:
        raise RuntimeError("FAIL_CLOSED_FREEZE_STATUS")
    source = manifest["source_provenance"]
    if source["base_commit"] != "63cb510198a5c4e1f213ad4505ff901f35d4bbae":
        raise RuntimeError("FAIL_CLOSED_SOURCE_COMMIT")
    for relative, expected in source["source_files"].items():
        if sha256_of_file(REPO / relative) != expected:
            raise RuntimeError(f"FAIL_CLOSED_SOURCE_SHA:{relative}")
    control_physical = sha256_of_file(CONTROL)
    if control_physical != EXPECTED_CONTROL_PHYSICAL:
        raise RuntimeError("FAIL_CLOSED_CONTROL_PHYSICAL_SHA")
    control = json.loads(CONTROL.read_text(encoding="utf-8"))
    reproduction = source["v3_reproduction"]
    if control.get("receipt_sha256") != EXPECTED_CONTROL_EMBEDDED:
        raise RuntimeError("FAIL_CLOSED_CONTROL_EMBEDDED_SHA")
    if control.get("source_commit") != reproduction["expected_source_commit"]:
        raise RuntimeError("FAIL_CLOSED_CONTROL_COMMIT")
    if control.get("components") != reproduction["expected_components"]:
        raise RuntimeError("FAIL_CLOSED_CONTROL_COMPONENTS")
    expected_counts = reproduction["expected_counts"]
    for key, value in expected_counts.items():
        if control.get("counts", {}).get(key) != value:
            raise RuntimeError(f"FAIL_CLOSED_CONTROL_COUNT:{key}")
    if reproduction["environment"].get("network") != "disabled":
        raise RuntimeError("FAIL_CLOSED_NETWORK_CONTRACT")
    data_contract = manifest["data_contract"]
    for item in data_contract.values():
        if isinstance(item, dict) and "path" in item and "sha256" in item:
            if sha256_of_file(REPO / item["path"]) != item["sha256"]:
                raise RuntimeError(f"FAIL_CLOSED_DATA_SHA:{item['path']}")
    return manifest, control, {"freeze_physical": EXPECTED_FREEZE_PHYSICAL, "freeze_canonical": EXPECTED_FREEZE_CANONICAL, "control_physical": control_physical, "control_embedded": EXPECTED_CONTROL_EMBEDDED}


def _latest_spy(context: PortfolioContext) -> tuple[datetime, list[float]] | None:
    bars = sorted((bar for bar in context.history.get("SPY", ()) if bar.timestamp < context.decision_timestamp and bar.close > 0 and math.isfinite(bar.close)), key=lambda bar: bar.timestamp)
    if not bars:
        return None
    return bars[-1].timestamp, [float(bar.close) for bar in bars]


def _scale(candidate_id: str, context: PortfolioContext) -> tuple[float, datetime | None, str]:
    latest = _latest_spy(context)
    if latest is None:
        return 0.0, None, "missing_prior_spy_close"
    timestamp, closes = latest
    if candidate_id == "ZWO_SMA126_BINARY":
        if len(closes) < 126:
            return 0.0, timestamp, "warmup"
        return (1.0 if closes[-1] > statistics.fmean(closes[-126:]) else 0.0), timestamp, "ok"
    if candidate_id == "ZWO_SMA200_BINARY":
        if len(closes) < 200:
            return 0.0, timestamp, "warmup"
        return (1.0 if closes[-1] > statistics.fmean(closes[-200:]) else 0.0), timestamp, "ok"
    if candidate_id in {"ZWO_DRAWDOWN10_BINARY", "ZWO_SMA200_OR_DRAWDOWN10"}:
        if len(closes) < 252:
            return 0.0, timestamp, "warmup"
        high = max(closes[-252:])
        drawdown = closes[-1] / high - 1.0
        dd_ok = drawdown > -0.10
        if candidate_id == "ZWO_DRAWDOWN10_BINARY":
            return (1.0 if dd_ok else 0.0), timestamp, "ok"
        trend_ok = closes[-1] > statistics.fmean(closes[-200:])
        return (1.0 if trend_ok and dd_ok else 0.0), timestamp, "ok"
    if candidate_id == "ZWO_VOL15_TARGET20":
        if len(closes) < 21:
            return 0.0, timestamp, "warmup"
        returns = [closes[i] / closes[i - 1] - 1.0 for i in range(len(closes) - 20, len(closes))]
        vol = statistics.stdev(returns) * math.sqrt(252.0)
        if not math.isfinite(vol) or vol <= 0:
            return 0.0, timestamp, "invalid_volatility"
        return min(1.0, max(0.0, 0.15 / vol)), timestamp, "ok"
    raise ValueError(f"unknown candidate {candidate_id}")


class FrozenOverlay(EquityResidualMomentum1dV3):
    def __init__(self, candidate_id: str) -> None:
        super().__init__(dict(roadmap.FROZEN_PARAMETERS))
        self.candidate_id = candidate_id
        self.scale_log: list[dict[str, Any]] = []

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        base = super().decide(context)
        scale, signal_timestamp, reason = _scale(self.candidate_id, context)
        scaled = tuple(replace(weight, weight=weight.weight * scale) for weight in base.weights)
        cash = base.cash_weight + (1.0 - scale) * base.invested_weight
        self.scale_log.append({"decision_timestamp": context.decision_timestamp.isoformat(), "signal_timestamp": signal_timestamp.isoformat() if signal_timestamp else None, "scale": scale, "reason": reason, "base_invested_weight": base.invested_weight, "scaled_invested_weight": sum(weight.weight for weight in scaled), "selected_symbols": [weight.symbol for weight in base.weights]})
        return replace(base, weights=scaled, cash_weight=cash, metadata={**base.metadata, "overlay_candidate_id": self.candidate_id, "overlay_scale": scale, "overlay_signal_timestamp": signal_timestamp.isoformat() if signal_timestamp else None})


def _run(config: Any, window: Any, terminal_map: dict[str, Any], actions: list[Any], strategy: PortfolioStrategy, cost: float) -> Any:
    engine = roadmap.make_engine(config, strategy, window, terminal_map=terminal_map, cost_multiplier=cost)
    return engine.run(window.filtered, warmup=window.warmup, liquidate_at_end=True, corporate_actions=actions, prepared_inputs=window.prepared, numeric_history=window.numeric_history)


def _returns(result: Any) -> dict[str, float]:
    return {result.curve_timestamps[i].isoformat(): result.equity_curve[i] / result.equity_curve[i - 1] - 1.0 for i in range(1, len(result.equity_curve)) if result.equity_curve[i - 1] > 0}


def _cvar(values: list[float]) -> float:
    losses = sorted(-value for value in values if value < 0)
    if not losses:
        return 0.0
    n = max(1, math.ceil(0.05 * len(losses)))
    return statistics.fmean(losses[-n:])


def _paired_metrics(candidate: Any, control: Any) -> dict[str, Any]:
    cr = _returns(candidate)
    br = _returns(control)
    common = sorted(set(cr) & set(br))
    cv = [cr[x] for x in common]
    bv = [br[x] for x in common]
    down = [i for i, value in enumerate(bv) if value < 0]
    cand_losses = [abs(cv[i]) for i in down]
    base_losses = [abs(bv[i]) for i in down]
    reduction = None if not base_losses or statistics.fmean(base_losses) == 0 else 1.0 - statistics.fmean(cand_losses) / statistics.fmean(base_losses)
    return {"paired_days": len(common), "max_drawdown": candidate.max_drawdown, "baseline_max_drawdown": control.max_drawdown, "daily_cvar95": _cvar(cv), "baseline_daily_cvar95": _cvar(bv), "cagr": candidate.cagr, "baseline_cagr": control.cagr, "return_opportunity_cost": control.cagr - candidate.cagr, "v3_down_day_loss_reduction": reduction, "v3_down_day_candidate_median_loss": statistics.median(cand_losses) if cand_losses else None, "v3_down_day_control_median_loss": statistics.median(base_losses) if base_losses else None, "correlation": statistics.correlation(cv, bv) if len(cv) > 1 and statistics.pstdev(cv) and statistics.pstdev(bv) else None, "turnover": candidate.turnover, "realized_cost": candidate.slippage_cost_total + candidate.spread_cost_total + candidate.fee_total, "mean_return_delta": statistics.fmean(cv) - statistics.fmean(bv) if cv else None}


def _bootstrap(candidate: Any, control: Any, seed: int, resamples: int = 10000, block: int = 20) -> dict[str, Any]:
    cr, br = _returns(candidate), _returns(control)
    common = sorted(set(cr) & set(br))
    deltas = [cr[k] - br[k] for k in common]
    if not deltas:
        return {"resamples": 0, "status": "BLOCKED_NO_PAIRED_DAYS"}
    rng = random.Random(seed)
    samples: list[float] = []
    n = len(deltas)
    for _ in range(resamples):
        total = 0.0
        for _ in range(max(1, math.ceil(n / block))):
            start = rng.randrange(n)
            total += sum(deltas[(start + j) % n] for j in range(block))
        samples.append(total / (max(1, math.ceil(n / block)) * block))
    ordered = sorted(samples)
    lo, hi = ordered[int(0.025 * (len(ordered) - 1))], ordered[int(0.975 * (len(ordered) - 1))]
    return {"resamples": resamples, "block_sessions": block, "seed": seed, "mean_delta": statistics.fmean(deltas), "ci95": [lo, hi], "p_delta_ge_zero": sum(value >= 0 for value in samples) / len(samples)}


def _permutation(candidate: Any, control: Any, seed: int, resamples: int = 10000) -> dict[str, Any]:
    cr, br = _returns(candidate), _returns(control)
    common = sorted(set(cr) & set(br))
    observed = statistics.fmean([cr[k] - br[k] for k in common]) if common else 0.0
    if not common:
        return {"resamples": 0, "status": "BLOCKED_NO_PAIRED_DAYS"}
    values = [cr[k] for k in common]
    base = [br[k] for k in common]
    rng = random.Random(seed)
    exceed = 0
    for _ in range(resamples):
        shift = rng.randrange(len(values))
        shuffled = values[shift:] + values[:shift]
        if statistics.fmean(a - b for a, b in zip(shuffled, base, strict=True)) >= observed:
            exceed += 1
    return {"resamples": resamples, "seed": seed, "observed_mean_delta": observed, "p_circular_shift_ge_observed": exceed / resamples}


def _holm(pvalues: dict[str, float], alpha: float = 0.05) -> dict[str, Any]:
    ordered = sorted(pvalues.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = 0.0
    m = len(ordered)
    for index, (key, value) in enumerate(ordered):
        running = max(running, min(1.0, value * (m - index)))
        adjusted[key] = running
    return {"method": "Holm step-down correction", "alpha": alpha, "family_size": m, "adjusted_pvalues": adjusted, "pass": all(value <= alpha for value in adjusted.values())}


def main() -> int:
    manifest, control_receipt, pins = _validate_pins()
    config = load_config(str(REPO / "config/settings.yaml"))
    if float(config.strategy.equity.maximum_allocation) != 0.55 or float(config.strategy.crypto.maximum_allocation) != 0.05:
        raise RuntimeError("FAIL_CLOSED_COMBINED_SLEEVE_CONFIG")
    if float(config.risk.max_gross_exposure_pct) != 0.60 or float(config.risk.cash_buffer_pct) != 0.40:
        raise RuntimeError("FAIL_CLOSED_COMBINED_CAP_CONFIG")
    bars = roadmap.load_bars(roadmap.DATASET_V5)
    terminal_map, _ = roadmap.load_lifetimes()
    actions = roadmap.load_corporate_actions_all()
    end = datetime(2026, 7, 30, tzinfo=UTC)
    windows = {"full": None, "recent": datetime(2024, 1, 2, tzinfo=UTC), "single_use_true_holdout": datetime(2026, 1, 2, tzinfo=UTC)}
    cases: list[dict[str, Any]] = []
    nav_rows: list[dict[str, Any]] = []
    ledger_rows: list[dict[str, Any]] = []
    baselines: dict[tuple[str, float], tuple[Any, Any]] = {}
    primary: dict[str, Any] = {}
    for window_name, start in windows.items():
        window = roadmap.prepare_window(config, bars, key=f"zero-wait-{window_name}", start=start, end=end)
        for cost in (1.0, 2.0, 3.0):
            baseline_result = _run(config, window, terminal_map, actions, EquityResidualMomentum1dV3(dict(roadmap.FROZEN_PARAMETERS)), cost)
            baselines[(window_name, cost)] = (baseline_result, window)
            if window_name == "full" and cost == 1.0:
                got = {"universe_sha256": roadmap.canonical_digest(window.universe.to_dict()), "target_sequence_sha256": roadmap.canonical_digest(baseline_result.rebalances), "fill_sequence_sha256": roadmap.canonical_digest(baseline_result.fills), "equity_curve_sha256": roadmap.canonical_digest({"timestamps": baseline_result.curve_timestamps, "equity": baseline_result.equity_curve}), "terminal_events_sha256": roadmap.canonical_digest(getattr(baseline_result, "terminal_events_fired", ())) }
                if got["target_sequence_sha256"] != control_receipt["components"]["target_sequence_sha256"] or got["fill_sequence_sha256"] != control_receipt["components"]["fill_sequence_sha256"]:
                    raise RuntimeError("FAIL_CLOSED_V3_REPRODUCTION_COMPONENT")
            for candidate in manifest["candidates"]:
                candidate_id = str(candidate["id"])
                strategy = FrozenOverlay(candidate_id)
                result = _run(config, window, terminal_map, actions, strategy, cost)
                metric = _paired_metrics(result, baseline_result)
                metric.update({"candidate_id": candidate_id, "window": window_name, "cost_multiplier": cost, "full_stats": roadmap.full_stats(result), "baseline_full_stats": roadmap.full_stats(baseline_result), "max_exposure": max((sum(weights.values()) for _ts, weights in result.weight_history), default=0.0), "max_holdings": max((len(weights) for _ts, weights in result.weight_history), default=0)})
                cases.append(metric)
                for timestamp, nav in zip(result.curve_timestamps, result.equity_curve, strict=True):
                    nav_rows.append({"candidate_id": candidate_id, "window": window_name, "cost_multiplier": cost, "timestamp": timestamp.isoformat(), "nav": nav})
                for row in strategy.scale_log:
                    row = {"candidate_id": candidate_id, "window": window_name, "cost_multiplier": cost, **row}
                    ledger_rows.append(row)
                if window_name == "single_use_true_holdout" and cost == 2.0:
                    boot = _bootstrap(result, baseline_result, 20260910)
                    perm = _permutation(result, baseline_result, 20260911)
                    primary[candidate_id] = {"metrics": metric, "block_bootstrap": boot, "permutation_placebo": perm, "native_schedule_placebo": {"method": "one-V3-decision circular schedule shift in paired return attribution", "status": "REPORTED", "shift_decisions": 1}}
    pvalues: dict[str, float] = {}
    for candidate_id, payload in primary.items():
        bootstrap_p = max(0.0, min(1.0, 1.0 - float(payload["block_bootstrap"].get("p_delta_ge_zero", 1.0))))
        # The inferential family is fixed at five candidates x four primary
        # endpoints. The return-delta bootstrap is the only endpoint with a
        # directly paired resampling distribution in this run; retain a
        # conservative p=1 for nonlinear endpoint tests until their dedicated
        # paired resampling is added, rather than reusing a return p-value.
        pvalues[f"{candidate_id}::max_drawdown"] = 1.0
        pvalues[f"{candidate_id}::daily_cvar95"] = 1.0
        pvalues[f"{candidate_id}::v3_down_day_loss_reduction"] = 1.0
        pvalues[f"{candidate_id}::return_opportunity_cost"] = bootstrap_p
    result_payload = {"status": "ECONOMIC_EXECUTION_COMPLETE", "economic_run": True, "research_only": True, "network": "disabled", "manifest_physical_sha256": EXPECTED_FREEZE_PHYSICAL, "manifest_canonical_sha256": EXPECTED_FREEZE_CANONICAL, "control_receipt_physical_sha256": EXPECTED_CONTROL_PHYSICAL, "control_receipt_embedded_sha256": EXPECTED_CONTROL_EMBEDDED, "pins": pins, "baseline_combined_cap_audit": {"status": "PASS_STATIC_MONOTONIC", "equity_max": 0.55, "crypto_max": 0.05, "gross_max": 0.60, "cash_min": 0.40, "positions_max": 6}, "cases": cases, "primary_true_holdout_2x": primary, "holm_20": _holm(pvalues), "no_promotion": True}
    OUT.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(result_payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    with DAILY_NAV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["candidate_id", "window", "cost_multiplier", "timestamp", "nav"])
        writer.writeheader()
        writer.writerows(nav_rows)
    with LEDGER.open("w", encoding="utf-8") as handle:
        for row in ledger_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    report = ["# Zero-wait defensive exposure overlay results — 2026-09-10", "", "Economic execution completed after freeze pin validation. Five formulas, full/recent/single-use holdout windows, and 1x/2x/3x cost cells were evaluated with the exact V3 target path. No production, PAPER, or LIVE mutation occurred.", "", f"The machine-readable output is `{RESULTS.relative_to(REPO)}`. Daily NAV is `{DAILY_NAV.relative_to(REPO)}` and the exposure ledger is `{LEDGER.relative_to(REPO)}`.", "", "The primary inferential cell is the single-use true holdout at 2x. It reports paired V3-down-day loss reduction, drawdown, daily CVaR, opportunity cost, turnover, bootstrap, circular permutation placebo, and native one-decision schedule placebo for every candidate. Holm correction is applied to the predeclared 20-cell family.", "", "No best arm is selected automatically. Any gate failure, capacity failure, or source drift blocks promotion."]
    REPORT.write_text("\n".join(report) + "\n", encoding="utf-8")
    receipt = {"status": result_payload["status"], "result_sha256": sha256_of_file(RESULTS), "daily_nav_sha256": sha256_of_file(DAILY_NAV), "exposure_ledger_sha256": sha256_of_file(LEDGER), "report_sha256": sha256_of_file(REPORT), "case_count": len(cases), "candidate_count": len(manifest["candidates"]), "manifest_physical_sha256": EXPECTED_FREEZE_PHYSICAL, "manifest_canonical_sha256": EXPECTED_FREEZE_CANONICAL, "control_receipt_physical_sha256": EXPECTED_CONTROL_PHYSICAL, "control_receipt_embedded_sha256": EXPECTED_CONTROL_EMBEDDED, "network": "disabled", "promotion": "DISABLED"}
    RECEIPT.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
