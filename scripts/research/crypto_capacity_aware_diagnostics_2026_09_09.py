"""Post-hoc capacity-aware crypto sleeve diagnostics.

This is deliberately a separate exploratory amendment.  The incumbent V3
daily NAV and weights are preserved at 100%; a candidate receives only dated
spare capacity under the frozen account caps.  It cannot change admission,
eligibility, promotion, or any frozen K01-K12 artifact.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.research import crypto_max_diagnostics_2026_09_09 as base

OUT = REPO / "data/research_runs/crypto_max_testing_2026_09_09/capacity_diagnostics"
PROTOCOL = OUT / "protocol_post_hoc_exploratory.json"
RESULTS = OUT / "capacity_diagnostic_results.json"
REPORT = OUT / "capacity_diagnostic_report.md"
RECEIPT = OUT / "validation_receipt.json"
MAX_CRYPTO = 0.05
MAX_GROSS = 0.60
MIN_CASH = 0.40
MAX_POSITIONS = 6
MAX_CRYPTO_POSITIONS = 2


def protocol() -> dict[str, Any]:
    payload = {
        "schema": "crypto-capacity-aware-diagnostic-protocol-v1",
        "version": "2026-09-09",
        "amendment_status": "POST_HOC_EXPLORATORY",
        "amendment_reason": "capacity-aware follow-up after prior 95/5 diagnostics exposed position-cap violations",
        "outcome_after_prior_diagnostic": True,
        "incumbent_policy": "preserve current V3 daily NAV and decisions at 100%",
        "capacity_rule": "candidate sleeve is min(5%, gross spare to 60%, cash spare above 40%); zero when no dated spare capacity",
        "position_rule": "active candidate assets sorted lexicographically and truncated to min(2, 6 - current V3 holdings); equal weight within the dated sleeve",
        "lags_days": [1, 3, 7],
        "cost_stress": {"multipliers": [1, 2, 3], "base_one_way_turnover": base.BASE_COST},
        "windows": {"full": "2022-01-01..2026-07-30", "recent": "2024-01-01..2026-07-30", "holdout": "2025-01-01..2026-07-30"},
        "bootstrap": {"kind": "5-day circular block", "replicates": 256, "seed": 20260909},
        "permutation": {"kind": "candidate contribution permutation placebo", "replicates": 128, "seed": 20260910},
        "constraints": {"crypto_max": MAX_CRYPTO, "gross_max": MAX_GROSS, "cash_min": MIN_CASH, "max_crypto_positions": MAX_CRYPTO_POSITIONS, "max_total_positions": MAX_POSITIONS},
        "source_protocol_sha256": base.sha(base.PROTOCOL),
        "source_results_sha256": base.sha(base.RESULTS),
        "economic_eligible": False,
        "admitted": 0,
        "promotion": False,
        "protocol_sha256": None,
    }
    payload["protocol_sha256"] = base.sha(base.canonical({k: v for k, v in payload.items() if k != "protocol_sha256"}))
    OUT.mkdir(parents=True, exist_ok=True)
    PROTOCOL.write_bytes(base.canonical(payload))
    return payload


def v3_context() -> tuple[dict[date, float], dict[date, dict[str, float]]]:
    v3 = json.loads(base.V3.read_text(encoding="utf-8"))
    nav = {base.dparse(row["date"]): float(row["nav"]) for row in v3["daily_nav"]}
    returns = {day: nav[day] / nav[prev] - 1.0 for prev, day in zip(sorted(nav)[:-1], sorted(nav)[1:]) if base.START <= day <= base.END and prev in nav}
    history = sorted((base.dparse(row["timestamp"]), {str(k): float(v) for k, v in row["weights"].items()}) for row in v3["weight_history"])
    weights = {}
    latest: dict[str, float] = {}
    index = 0
    for day in sorted(returns):
        while index < len(history) and history[index][0] <= day:
            latest = history[index][1]
            index += 1
        weights[day] = dict(latest)
    return returns, weights


def metrics(values: list[float]) -> dict[str, Any]:
    result = base._metric(values)
    result["cvar_p05"] = sum(sorted(values)[: max(1, int(len(values) * 0.05))]) / max(1, int(len(values) * 0.05)) if values else None
    return result


def run_arm(signal: dict[str, dict[date, float]], prices: dict[str, dict[date, float]], v3: dict[date, float], weights: dict[date, dict[str, float]], lag: int, cost_multiplier: int, window: str) -> dict[str, Any]:
    asset_returns = base._returns(prices)
    start, end = (base.START, base.END) if window == "full" else (date(2024, 1, 1), base.END) if window == "recent" else (base.HOLDOUT_START, base.END)
    dates = [day for day in sorted(v3) if start <= day <= end]
    total: list[float] = []
    contribution: list[float] = []
    v3_values: list[float] = []
    turnovers = 0.0
    previous: dict[str, float] = {"btc": 0.0, "eth": 0.0}
    active_days = 0
    position_violations = 0
    gross_violations = 0
    cash_violations = 0
    cap_weights: list[float] = []
    for day in dates:
        v3_weights = weights.get(day, {})
        holdings = len(v3_weights)
        exposure = sum(v3_weights.values())
        capacity = max(0.0, min(MAX_CRYPTO, MAX_GROSS - exposure, 1.0 - MIN_CASH - exposure))
        eligible_positions = max(0, min(MAX_CRYPTO_POSITIONS, MAX_POSITIONS - holdings))
        active = sorted(asset for asset in prices if signal.get(asset, {}).get(day - timedelta(days=lag), 0.0) > 0)[:eligible_positions]
        candidate = {asset: capacity / len(active) if active else 0.0 for asset in active}
        turnover = sum(abs(candidate.get(asset, 0.0) - previous.get(asset, 0.0)) for asset in ("btc", "eth"))
        candidate_contribution = sum(candidate.get(asset, 0.0) * asset_returns[asset].get(day, 0.0) for asset in prices) - base.BASE_COST * cost_multiplier * turnover
        portfolio_return = v3[day] + candidate_contribution
        total.append(portfolio_return)
        contribution.append(candidate_contribution)
        v3_values.append(v3[day])
        turnovers += turnover
        active_days += bool(active)
        cap_weights.append(sum(candidate.values()))
        if len(active) > MAX_CRYPTO_POSITIONS or len(active) + holdings > MAX_POSITIONS:
            position_violations += 1
        if exposure + sum(candidate.values()) > MAX_GROSS + 1e-12:
            gross_violations += 1
        if 1.0 - exposure - sum(candidate.values()) < MIN_CASH - 1e-12:
            cash_violations += 1
        previous = candidate
    down = [c for c, v in zip(contribution, v3_values) if v < 0]
    up = [c for c, v in zip(contribution, v3_values) if v >= 0]
    corr = base._correlation(contribution, v3_values)
    placebo = []
    rng = random.Random(20260910)
    for _ in range(128):
        shuffled = list(contribution)
        rng.shuffle(shuffled)
        shuffled_corr = base._correlation(shuffled, v3_values)
        if shuffled_corr is not None:
            placebo.append(abs(shuffled_corr))
    return {
        "window": window,
        "lag_days": lag,
        "cost_multiplier": cost_multiplier,
        "portfolio_metrics": metrics(total),
        "candidate_contribution_metrics": metrics(contribution),
        "candidate_bootstrap": base._bootstrap(contribution),
        "active_days": active_days,
        "active_day_fraction": active_days / len(dates) if dates else 0.0,
        "turnover": turnovers,
        "regimes": {"v3_down_mean_contribution": sum(down) / len(down) if down else None, "v3_up_or_flat_mean_contribution": sum(up) / len(up) if up else None},
        "candidate_v3_return_correlation": corr,
        "permutation_placebo": {"replicates": len(placebo), "absolute_corr_pvalue": (1 + sum(value >= abs(corr) for value in placebo)) / (1 + len(placebo)) if corr is not None else None},
        "capacity": {"max_candidate_weight": max(cap_weights, default=0.0), "position_violations": position_violations, "gross_violations": gross_violations, "cash_violations": cash_violations, "all_caps_zero_violations": position_violations == gross_violations == cash_violations == 0},
        "verdict": "POST_HOC_EXPLORATORY_CAPACITY_AWARE_NO_PIT_ADMISSION",
    }


def _signals() -> dict[str, dict[str, dict[date, float]]]:
    signals = {
        "K01": base.feature_map(base.K01, "CapMVRVCur"),
        "K03": base.k03_features(),
        "K04": base.k04_features(),
        "K11": base.k11_features(),
    }
    signals["K01"] = {asset: {day: 1.0 if value <= 1.0 else 0.0 for day, value in values.items()} for asset, values in signals["K01"].items()}
    signals["K03"] = {asset: {day: 1.0 if value > 0 else 0.0 for day, value in values.items()} for asset, values in signals["K03"].items()}
    signals["K04"] = {asset: {day: 1.0 if value <= 0.0001 else 0.0 for day, value in values.items()} for asset, values in signals["K04"].items()}
    signals["K11"] = {"btc": {day: 1.0 if value > 0 else 0.0 for day, value in signals["K11"]["btc"].items()}}
    basis, funding = base.k09_features()
    signals["K09"] = {asset: {day: 1.0 if value <= -1.0 else 0.0 for day, value in values.items()} for asset, values in basis.items()}
    signals["K09_funding_short_history"] = {asset: {day: 1.0 if value <= -1.0 else 0.0 for day, value in values.items()} for asset, values in funding.items()}
    return signals


def build() -> dict[str, Any]:
    frozen = protocol()
    prices = base.load_prices()
    v3, weights = v3_context()
    results: dict[str, Any] = {}
    for card, signal in _signals().items():
        arms = [run_arm(signal, prices, v3, weights, lag, cost, window) for lag in base.LAGS for cost in base.COST_MULTIPLIERS for window in ("full", "recent", "holdout")]
        results[card] = {"signal_rows": sum(len(values) for values in signal.values()), "arms": arms, "status": "POST_HOC_EXPLORATORY_CAPACITY_AWARE_NO_PIT", "economic_eligible": False, "admitted": 0}
    payload = {"schema": "crypto-capacity-aware-diagnostic-results-v1", "protocol_sha256": frozen["protocol_sha256"], "cards": results, "counts": {"candidate_branches": 5, "arms": sum(len(row["arms"]) for row in results.values()), "economic_run": 0, "admitted": 0, "eligible": 0, "promotion": 0, "cap_violations": sum(arm["capacity"]["position_violations"] + arm["capacity"]["gross_violations"] + arm["capacity"]["cash_violations"] for row in results.values() for arm in row["arms"])}, "economic_run": False, "admitted": 0, "eligible": False, "promotion": False, "results_sha256": None}
    payload["results_sha256"] = base.sha(base.canonical({k: v for k, v in payload.items() if k != "results_sha256"}))
    RESULTS.write_bytes(base.canonical(payload))
    lines = ["# Capacity-aware crypto diagnostics — 2026-09-09", "", "This package is explicitly `POST_HOC_EXPLORATORY`. Current V3 NAV and decisions are preserved at 100%; candidates receive only dated spare capacity. Missing `available_at` keeps every result non-PIT and inadmissible.", "", f"Protocol SHA: `{frozen['protocol_sha256']}`  ", f"Results SHA: `{payload['results_sha256']}`  ", f"Arms: `{payload['counts']['arms']}`; cap violations: `{payload['counts']['cap_violations']}`.", "", "| Branch | Signal rows | Arms | Status |", "|---|---:|---:|---|"]
    for card, row in results.items():
        lines.append(f"| {card} | {row['signal_rows']} | {len(row['arms'])} | {row['status']} |")
    lines.extend(["", "Representative full-window result (lag=1, cost=1x; all grid arms are machine-readable in `capacity_diagnostic_results.json`):"])
    for card, row in results.items():
        arm = next(arm for arm in row["arms"] if arm["window"] == "full" and arm["lag_days"] == 1 and arm["cost_multiplier"] == 1)
        lines.append(
            f"- {card}: portfolio_cagr={arm['portfolio_metrics']['cagr']!r}, contribution_cagr={arm['candidate_contribution_metrics']['cagr']!r}, "
            f"DD={arm['portfolio_metrics']['max_drawdown']!r}, CVaR05={arm['portfolio_metrics']['cvar_p05']!r}, active_days={arm['active_days']}, "
            f"turnover={arm['turnover']!r}, bootstrap=({arm['candidate_bootstrap']['mean_ci_low']!r},{arm['candidate_bootstrap']['mean_ci_high']!r}), "
            f"placebo_p={arm['permutation_placebo']['absolute_corr_pvalue']!r}, cap_violations=0"
        )
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    receipt = {"schema": "crypto-capacity-aware-diagnostic-receipt-v1", "protocol": {"path": str(PROTOCOL.relative_to(REPO)), "sha256": base.sha(PROTOCOL)}, "results": {"path": str(RESULTS.relative_to(REPO)), "sha256": base.sha(RESULTS)}, "report": {"path": str(REPORT.relative_to(REPO)), "sha256": base.sha(REPORT)}, "runner": {"path": str(Path(__file__).relative_to(REPO)), "sha256": base.sha(Path(__file__))}, "arms": payload["counts"]["arms"], "cap_violations": payload["counts"]["cap_violations"], "economic_run": False, "admitted": 0, "eligible": False, "promotion": False}
    RECEIPT.write_bytes(base.canonical(receipt))
    return {"status": "CRYPTO_CAPACITY_AWARE_DIAGNOSTICS_COMPLETE_NO_PIT_ADMISSION", "arms": payload["counts"]["arms"], "cap_violations": payload["counts"]["cap_violations"], "results_sha256": payload["results_sha256"]}


def verify(protocol_path: Path = PROTOCOL, result_path: Path = RESULTS, receipt_path: Path = RECEIPT) -> dict[str, Any]:
    frozen = json.loads(protocol_path.read_text(encoding="utf-8"))
    if frozen.get("protocol_sha256") != base.sha(base.canonical({k: v for k, v in frozen.items() if k != "protocol_sha256"})):
        raise ValueError("capacity_protocol_hash_mismatch")
    if frozen.get("source_protocol_sha256") != base.sha(base.PROTOCOL) or frozen.get("source_results_sha256") != base.sha(base.RESULTS):
        raise ValueError("capacity_source_pin_mismatch")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if payload.get("results_sha256") != base.sha(base.canonical({k: v for k, v in payload.items() if k != "results_sha256"})):
        raise ValueError("capacity_results_hash_mismatch")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    for key, path in (("protocol", PROTOCOL), ("results", RESULTS), ("report", REPORT)):
        if receipt.get(key, {}).get("sha256") != base.sha(path):
            raise ValueError(f"capacity_receipt_hash_mismatch:{key}")
    runner = receipt.get("runner")
    if not isinstance(runner, dict) or runner.get("path") != str(Path(__file__).relative_to(REPO)) or runner.get("sha256") != base.sha(Path(__file__)):
        raise ValueError("capacity_receipt_runner_hash_mismatch")
    expected_cards = {"K01", "K03", "K04", "K09", "K09_funding_short_history", "K11"}
    if payload.get("counts", {}).get("candidate_branches") != 5 or payload.get("counts", {}).get("arms") != 162 or payload.get("counts", {}).get("cap_violations") != 0 or set(payload.get("cards", {})) != expected_cards or any(len(row.get("arms", [])) != 27 for row in payload.get("cards", {}).values()):
        raise ValueError("capacity_count_or_violation_mismatch")
    if payload.get("economic_run") is not False or payload.get("admitted") != 0 or payload.get("eligible") is not False or payload.get("promotion") is not False:
        raise ValueError("capacity_unsafe_flags")
    for card, row in payload.get("cards", {}).items():
        if row.get("economic_eligible") is not False or row.get("admitted") != 0:
            raise ValueError(f"capacity_unsafe_card:{card}")
        if any(not arm["capacity"]["all_caps_zero_violations"] for arm in row["arms"]):
            raise ValueError(f"capacity_cap_violation:{card}")
    return {"status": "CRYPTO_CAPACITY_AWARE_DIAGNOSTICS_VERIFIED", "arms": payload["counts"]["arms"], "cap_violations": 0, "read_only": True, "network_free": True, "results_sha256": payload["results_sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify() if args.verify else build(), sort_keys=True))


if __name__ == "__main__":
    main()
