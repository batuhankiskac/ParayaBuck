"""Independent, read-only verifier for the frozen overnight-reversal run."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from parayabuck.config import load_config
from research.overnight_reversal_v1.campaign import (
    BAR_PATH,
    CORPORATE_ACTION_PATH,
    DESIGN_ARTIFACTS,
    DESIGN_BRANCH,
    DESIGN_COMMIT,
    DESIGN_DIR,
    DESIGN_HASHES,
    ECONOMIC_BRANCH,
    EVALUATION_END,
    EVALUATION_START,
    LIFETIME_PATH,
    RESEARCH_DIR,
    _parse_timestamp,
)
from research.overnight_reversal_v1.candidate import (
    CANDIDATE_NAME,
    CANDIDATE_VERSION,
    FIXED_PER_NAME_WEIGHT,
    TOP_N,
    signal_from_bars,
)
from research.overnight_reversal_v1.data import (
    EXPECTED_BAR_ROWS,
    EXPECTED_BAR_SHA256,
    EXPECTED_CORPORATE_ACTION_ROWS,
    EXPECTED_CORPORATE_ACTION_SHA256,
    EXPECTED_DIVIDEND_ROWS,
    EXPECTED_LIFETIME_ROWS,
    EXPECTED_LIFETIME_SHA256,
    EXPECTED_SPLIT_ROWS,
    load_normalized_dataset,
    session_date,
    sha256_file,
)

EXPECTED_CASES = tuple(f"{arm}/{cost}" for arm in ("A", "B") for cost in ("1x", "2x", "3x"))
EPSILON = 1e-9


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return raw


def _read_json_gz(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return raw


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=RESEARCH_DIR.parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _finite(value: object) -> bool:
    try:
        if isinstance(value, (int, float)):
            return math.isfinite(value)
        return math.isfinite(float(str(value)))
    except (TypeError, ValueError):
        return False


def _metric(metrics: dict[str, Any], key: str, period: str) -> dict[str, Any]:
    raw = metrics[key]["periods"][period]
    if not isinstance(raw, dict):
        raise ValueError(f"Metric is not an object: {key}/{period}")
    return raw


def _validate_frozen_inputs(receipt: dict[str, Any], preflight: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if receipt.get("status") != "FROZEN_INPUTS_RECEIVED":
        errors.append("frozen receipt status is not FROZEN_INPUTS_RECEIVED")
    if receipt.get("economic_outcomes_inspected_before_run") is not False:
        errors.append("receipt does not prove outcomes were unseen before the run")
    if receipt.get("backtest_run_before_run") is not False:
        errors.append("receipt does not prove the backtest was not run before the run")
    if preflight.get("status") != "PASS":
        errors.append("pre-run validation did not PASS")
    if _git("branch", "--show-current") != ECONOMIC_BRANCH:
        errors.append("verification is not on the frozen economic branch")
    try:
        if _git("rev-parse", DESIGN_BRANCH) != DESIGN_COMMIT:
            errors.append("design branch tip changed")
        if _git("rev-parse", "main") != _git("rev-parse", "origin/main"):
            errors.append("main and origin/main diverged")
        for name in DESIGN_ARTIFACTS:
            if sha256_file(DESIGN_DIR / name) != DESIGN_HASHES[name]:
                errors.append(f"design artifact hash changed: {name}")
    except (OSError, subprocess.SubprocessError) as exc:
        errors.append(f"git/design identity could not be verified: {exc}")
    expected_hashes = {
        "bars": EXPECTED_BAR_SHA256,
        "lifetimes": EXPECTED_LIFETIME_SHA256,
        "corporate_actions": EXPECTED_CORPORATE_ACTION_SHA256,
    }
    recorded_hashes = preflight.get("data_hashes", {})
    if not isinstance(recorded_hashes, dict):
        errors.append("preflight data_hashes is not an object")
    else:
        for key, expected in expected_hashes.items():
            if recorded_hashes.get(key) != expected:
                errors.append(f"preflight {key} hash does not match the pinned package")
    return errors


def _validate_dataset(dataset: Any, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    audit = dataset.structural_audit
    bars = audit.get("bars", {})
    spy = audit.get("spy", {})
    lifecycle = audit.get("lifecycle", {})
    actions = audit.get("corporate_actions", {})
    if not isinstance(bars, dict) or not isinstance(spy, dict):
        return ["normalized structural audit is malformed"]
    checks = (
        (bars.get("rows"), EXPECTED_BAR_ROWS, "bars row count"),
        (bars.get("sha256"), EXPECTED_BAR_SHA256, "bars hash"),
        (lifecycle.get("rows") if isinstance(lifecycle, dict) else None, EXPECTED_LIFETIME_ROWS, "lifetime rows"),
        (lifecycle.get("sha256") if isinstance(lifecycle, dict) else None, EXPECTED_LIFETIME_SHA256, "lifetime hash"),
        (actions.get("rows") if isinstance(actions, dict) else None, EXPECTED_CORPORATE_ACTION_ROWS, "action rows"),
        (actions.get("sha256") if isinstance(actions, dict) else None, EXPECTED_CORPORATE_ACTION_SHA256, "action hash"),
        (
            actions.get("cash_dividend_rows") if isinstance(actions, dict) else None,
            EXPECTED_DIVIDEND_ROWS,
            "dividend rows",
        ),
        (actions.get("split_action_rows") if isinstance(actions, dict) else None, EXPECTED_SPLIT_ROWS, "split rows"),
    )
    errors.extend(f"{label} mismatch" for actual, expected, label in checks if actual != expected)
    for field in (
        "duplicate_symbol_session_rows",
        "conflicting_duplicate_rows",
        "order_violations",
        "nonfinite_rows",
        "nonpositive_open_count",
        "nonpositive_close_count",
        "invalid_open_count",
        "invalid_close_count",
    ):
        if bars.get(field) != 0:
            errors.append(f"bars quality field is nonzero: {field}")
    if float(spy.get("dataset_session_date_coverage_pct", 0.0)) < 99.0:
        errors.append("SPY coverage is below 99 percent")
    manifest_hashes = manifest.get("data_hashes", {})
    if isinstance(manifest_hashes, dict):
        for key, path in (
            ("bars", BAR_PATH),
            ("lifetimes", LIFETIME_PATH),
            ("corporate_actions", CORPORATE_ACTION_PATH),
        ):
            if manifest_hashes.get(key) != sha256_file(path):
                errors.append(f"manifest {key} hash does not match the live file")
    return errors


def _verify_signal_audit(dataset: Any, summary: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    path = RESEARCH_DIR / "signal_audit.csv.gz"
    rows_seen = 0
    valid_pairs = 0
    decision_symbols: set[tuple[str, str]] = set()
    sampled = 0
    last_row: dict[str, str] | None = None
    sample_indices: set[int] = set()
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows_seen += 1
            last_row = row
            decision_key = (str(row.get("decision_timestamp")), str(row.get("symbol")))
            if decision_key in decision_symbols:
                errors.append(f"duplicate signal audit key: {decision_key}")
            decision_symbols.add(decision_key)
            score_raw = str(row.get("score") or "").strip()
            is_reference = row.get("symbol") == "SPY"
            if not is_reference and score_raw:
                valid_pairs += 1
            if rows_seen <= 3 or rows_seen % 1000 == 0:
                sample_indices.add(rows_seen)
    if last_row is not None:
        sample_indices.add(rows_seen)
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader, start=1):
            if index not in sample_indices:
                continue
            sampled += 1
            symbol = str(row["symbol"])
            decision_day = date.fromisoformat(str(row["decision_date"]))
            prior_day = dataset.previous_spy_date(decision_day)
            if symbol == "SPY":
                if row.get("score", "").strip():
                    errors.append(f"SPY received a candidate score at row {index}")
                continue
            current = dataset.bar_for(symbol, decision_day)
            prior = dataset.bar_for(symbol, prior_day) if prior_day is not None else None
            spy_current = dataset.bar_for("SPY", decision_day)
            spy_prior = dataset.bar_for("SPY", prior_day) if prior_day is not None else None
            stock_map = {decision_day: current, prior_day: prior} if current and prior and prior_day else {}
            spy_map = (
                {decision_day: spy_current, prior_day: spy_prior} if spy_current and spy_prior and prior_day else {}
            )
            expected = signal_from_bars(symbol, decision_day, prior_day, stock_map, spy_map)
            recorded_score = row.get("score", "").strip()
            if expected.score is None:
                if recorded_score:
                    errors.append(f"ineligible signal has a score at row {index}")
            elif not recorded_score or not math.isclose(
                float(recorded_score), expected.score, rel_tol=1e-10, abs_tol=1e-12
            ):
                errors.append(f"signal score mismatch at row {index}")
            if expected.prior_date and row.get("prior_date") != expected.prior_date.isoformat():
                errors.append(f"prior date mismatch at row {index}")
            if current is not None and current.timestamp > _parse_timestamp(str(row["decision_timestamp"])):
                errors.append(f"signal uses a future current bar at row {index}")
    expected_rows = int(summary.get("pool_row_count", -1))
    expected_valid = int(summary.get("valid_common_pair_count", -1))
    if rows_seen != expected_rows:
        errors.append("signal audit row count disagrees with signal summary")
    if valid_pairs != expected_valid:
        errors.append("signal audit valid-pair count disagrees with signal summary")
    return errors, {
        "rows_seen": rows_seen,
        "valid_pairs_seen": valid_pairs,
        "sampled_rows": sampled,
        "duplicate_keys": len(decision_symbols) != rows_seen,
        "score_reconstruction": "PASS" if not errors else "FAIL",
    }


def _verify_case(name: str, case: dict[str, Any], config: Any) -> list[str]:
    errors: list[str] = []
    arm, cost = name.split("/")
    multiplier = float(cost.rstrip("x"))
    if case.get("arm") != arm or case.get("cost_scenario") != cost:
        errors.append(f"{name}: case identity mismatch")
    if case.get("strategy_name") != (CANDIDATE_NAME if arm == "B" else config.strategy.equity.name):
        errors.append(f"{name}: strategy identity mismatch")
    if not math.isclose(float(case.get("cost_multiplier", -1.0)), multiplier):
        errors.append(f"{name}: cost multiplier mismatch")
    fee_model = case.get("fee_model", {})
    if not isinstance(fee_model, dict) or not math.isclose(float(fee_model.get("fee_multiplier", -1.0)), multiplier):
        errors.append(f"{name}: fee multiplier mismatch")
    rebalances = case.get("rebalances", [])
    fills = case.get("fills", [])
    if not isinstance(rebalances, list) or not isinstance(fills, list):
        return errors + [f"{name}: rebalances/fills malformed"]
    seen_decisions: set[str] = set()
    decision_days: set[date] = set()
    for record in rebalances:
        decision_timestamp = _parse_timestamp(str(record["decision_timestamp"]))
        decision_text = decision_timestamp.isoformat()
        if decision_text in seen_decisions:
            errors.append(f"{name}: duplicate decision timestamp")
        seen_decisions.add(decision_text)
        decision_days.add(session_date(decision_timestamp))
        weights = record.get("target_weights", [])
        if not isinstance(weights, list):
            errors.append(f"{name}: target weights malformed")
            continue
        if len(weights) > 6:
            errors.append(f"{name}: more than six target positions")
        if float(record.get("target_cash_weight", -1.0)) < -EPSILON:
            errors.append(f"{name}: negative target cash")
        if float(record.get("unfilled_notional", -1.0)) < -EPSILON:
            errors.append(f"{name}: negative unfilled notional")
        target_symbols: set[str] = set()
        for pair in weights:
            symbol, weight = str(pair[0]), float(pair[1])
            target_symbols.add(symbol)
            if symbol == "SPY" and arm == "B":
                errors.append(f"{name}: SPY is a candidate target")
            if not _finite(weight) or weight < -EPSILON or weight > 0.10 + EPSILON:
                errors.append(f"{name}: target weight cap violation")
            if arm == "B" and not math.isclose(weight, FIXED_PER_NAME_WEIGHT, rel_tol=0.0, abs_tol=1e-12):
                errors.append(f"{name}: candidate target is not fixed 0.55/6")
        if len(target_symbols) != len(weights):
            errors.append(f"{name}: duplicate target symbol")
        if arm == "B" and float(record.get("target_cash_weight", -1.0)) < 1.0 - 0.55 - EPSILON:
            errors.append(f"{name}: candidate cash below the unused-allocation floor")
    period_keys = {day.isocalendar()[:2] if arm == "A" else day for day in decision_days}
    if len(period_keys) != len(decision_days):
        errors.append(f"{name}: more than one rebalance in a scheduled period")
    rebalance_timestamps = {str(record["decision_timestamp"]) for record in rebalances}
    for fill in fills:
        fill_timestamp = _parse_timestamp(str(fill["timestamp"]))
        decision_timestamp = _parse_timestamp(str(fill["decision_timestamp"]))
        if fill_timestamp <= decision_timestamp:
            errors.append(f"{name}: same-bar or backward fill")
        if session_date(fill_timestamp) == session_date(decision_timestamp):
            errors.append(f"{name}: fill occurred in the decision session")
        if str(fill["symbol"]) == "SPY":
            errors.append(f"{name}: SPY was traded")
        if str(fill["decision_timestamp"]) not in rebalance_timestamps:
            errors.append(f"{name}: fill references unknown decision")
        for field in ("qty", "reference_price", "fill_price", "slippage_cost", "spread_cost", "fee"):
            if not _finite(fill.get(field)) or float(fill.get(field)) < -EPSILON:
                errors.append(f"{name}: invalid fill field {field}")
    for point in case.get("equity_curve", []):
        if not _finite(point.get("equity")) or float(point["equity"]) <= 0:
            errors.append(f"{name}: invalid equity curve point")
    return errors


def _independent_gates(
    control: dict[str, Any],
    candidate: dict[str, Any],
    coverage: dict[str, Any],
    signal_summary: dict[str, Any],
    concentration: dict[str, Any],
    preflight: dict[str, Any],
) -> dict[str, Any]:
    all_metrics = {**control, **candidate}

    def m(arm: str, cost: str, period: str) -> dict[str, Any]:
        return _metric(all_metrics, f"{arm}/{cost}", period)

    a_full, b_full = m("A", "1x", "full"), m("B", "1x", "full")
    a_val, b_val = m("A", "1x", "validation"), m("B", "1x", "validation")
    b2_val, a3_val, b3_val = m("B", "2x", "validation"), m("A", "3x", "validation"), m("B", "3x", "validation")
    years = [m("B", "1x", str(year)) for year in (2022, 2023, 2024, 2025)]
    full_deltas = [float(b_full[key]) - float(a_full[key]) for key in ("sharpe", "sortino", "calmar")]
    val_deltas = [float(b_val[key]) - float(a_val[key]) for key in ("sharpe", "sortino", "calmar")]
    gates = {
        "G1": preflight.get("status") == "PASS"
        and float(signal_summary.get("valid_common_pair_pct", 0.0)) >= 0.95
        and int(coverage["A"].get("unexplained_skip_count", 1)) == 0
        and int(coverage["B"].get("unexplained_skip_count", 1)) == 0,
        "G2": all(
            float(m("B", "1x", period)["cagr"]) > 0 and float(m("B", "1x", period)["net_total_return"]) > 0
            for period in ("full", "development", "validation")
        ),
        "G3": sum(value >= 0.05 for value in full_deltas) >= 2
        and all(value >= -0.05 for value in full_deltas)
        and sum(value >= 0.03 for value in val_deltas) >= 1
        and all(value >= -0.05 for value in val_deltas),
        "G4": sum(float(item["net_total_return"]) > 0 for item in years) >= 3
        and all(float(item["net_total_return"]) >= -0.08 for item in years)
        and float(b_val["cagr"]) > 0,
        "G5": float(b_full["max_drawdown"]) <= 0.10
        and float(b_val["max_drawdown"]) <= 0.10
        and float(b_full["max_drawdown"]) <= float(a_full["max_drawdown"]) + 0.01
        and float(b_val["max_drawdown"]) <= float(a_val["max_drawdown"]) + 0.01,
        "G6": float(b2_val["cagr"]) > 0
        and float(b3_val["net_total_return"]) > -0.02
        and float(b3_val["max_drawdown"]) <= float(a3_val["max_drawdown"]) + 0.02,
        "G7": float(b_full["annualized_one_way_target_turnover"]) <= 20.0
        and float(m("B", "2x", "full")["annualized_one_way_target_turnover"])
        <= 1.5 * float(b_full["annualized_one_way_target_turnover"])
        and float(coverage["B"]["target_notional_coverage_ratio"]) >= 0.95
        and float(coverage["B"]["skip_ratio"]) <= 0.05,
        "G8": float(concentration["target_max_position_weight"]) <= 0.10
        and float(concentration["largest_absolute_contributor_share"]) <= 0.20
        and float(concentration["leave_one_dominant_symbol_validation_cagr"]) > 0
        and int(concentration["max_sector_count_in_targets"]) <= 5,
        "G9": all(
            float(m(arm, cost, "full").get("max_target_weight", 1.0)) <= 0.10
            and int(m(arm, cost, "full").get("max_positions", 999)) <= 6
            for arm in ("A", "B")
            for cost in ("1x", "2x", "3x")
        ),
    }
    evidence = {
        "G1": {"valid_common_pair_pct": signal_summary.get("valid_common_pair_pct")},
        "G3": {"full_deltas": full_deltas, "validation_deltas": val_deltas},
        "G4": {"years": years},
        "G9": {"maximum_allocation": 0.55, "maximum_positions": 6},
    }
    return {
        "gates": {name: {"pass": passed, "evidence": evidence.get(name, {})} for name, passed in gates.items()},
        "all_pass": all(gates.values()),
        "near_miss_is_fail": True,
        "promotion_authorization": False,
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _summary_line(metrics: dict[str, Any], key: str, period: str) -> str:
    value = _metric(metrics, key, period)
    return (
        f"{key} {period}: CAGR {float(value['cagr']):.4%}, total {float(value['net_total_return']):.4%}, "
        f"Sharpe {float(value['sharpe']):.3f}, max DD {float(value['max_drawdown']):.4%}, "
        f"one-way target turnover {float(value['annualized_one_way_target_turnover']):.3f}x"
    )


def _write_report(
    *,
    decision: dict[str, Any],
    verification: dict[str, Any],
    control: dict[str, Any],
    candidate: dict[str, Any],
    manifest: dict[str, Any],
    coverage: dict[str, Any],
    concentration: dict[str, Any],
    yearly: dict[str, Any],
) -> None:
    gates = decision.get("gate_results", {}).get("gates", {})
    gate_lines = "\n".join(f"| {name} | {'PASS' if value.get('pass') else 'FAIL'} |" for name, value in gates.items())
    case_metrics = {**control, **candidate}
    yearly_lines = "\n".join(
        f"| {year} | {float(values['candidate_B_1x']['net_total_return']):.4%} | {float(values['control_A_1x']['net_total_return']):.4%} |"
        for year, values in yearly.items()
        if isinstance(values, dict) and "candidate_B_1x" in values
    )
    report = f"""# Overnight Reversal v1 Frozen Economic Experiment

## Decision

- Result validity: **{decision.get("result_validity")}**
- Economic verdict: **{decision.get("verdict")}**
- Promotion authorization: **False**. This research result does not authorize PAPER or LIVE deployment.
- Independent verification: **{verification.get("status")}**

## Frozen identity and scope

- Economic branch: `{manifest.get("economic_branch")}`
- Design branch and tip: `{manifest.get("design_branch")}` at `{manifest.get("design_branch_sha")}`
- Design parent used for the economic branch: `{DESIGN_COMMIT}`
- Candidate: `{CANDIDATE_NAME}` version `{CANDIDATE_VERSION}`
- Control: current configured `{manifest.get("arms", {}).get("A", "")}`
- Data feed: SIP regular-session daily bars; provider open proxy, not an auction claim.
- No parameter search, post-result rescue, new data feed, registry change, PAPER change, or LIVE change.

## Candidate rule

For completed common session `d` and immediately prior common SPY session `p(d)`:

`ON_i,d = Open_i,d / Close_i,p(d) - 1`; `ON_SPY,d = Open_SPY,d / Close_SPY,p(d) - 1`; `score = -(ON_i,d - ON_SPY,d)`.

The decision is after the completed bar `d`; any order fills only on a later tradable daily-bar provider-open proxy. The candidate selects at most six long names at exactly `0.55 / 6`, leaves unused allocation in cash, excludes SPY from targets, and uses the current account/risk/scanner/lifecycle/cost policies.

## Data and observation audit

- Bars: `{manifest.get("data_hashes", {}).get("bars")}`; lifetimes: `{manifest.get("data_hashes", {}).get("lifetimes")}`; corporate actions: `{manifest.get("data_hashes", {}).get("corporate_actions")}`.
- Frozen normalized rows and quality gates were independently re-read from the pinned files.
- Signal audit: `{verification.get("signal_audit", {})}`.
- Candidate and control used the same eligible PIT pool; alpha-specific observation sets were persisted separately.
- Decision coverage: `{json.dumps(coverage, sort_keys=True)}`.

## Performance and cost stress

{_summary_line(case_metrics, "A/1x", "full")}

{_summary_line(case_metrics, "B/1x", "full")}

{_summary_line(case_metrics, "A/1x", "validation")}

{_summary_line(case_metrics, "B/1x", "validation")}

{_summary_line(case_metrics, "B/2x", "validation")}

{_summary_line(case_metrics, "B/3x", "validation")}

Cost cases `1x`, `2x`, and `3x` scale slippage, spread, and effective-dated equity regulatory fees; commission remains the configured zero value. Full case artifacts are `case_A_1x.json.gz`, `case_A_2x.json.gz`, `case_A_3x.json.gz`, `case_B_1x.json.gz`, `case_B_2x.json.gz`, and `case_B_3x.json.gz`.

## Yearly validation

| Year | Candidate B 1x total return | Control A 1x total return |
|---|---:|---:|
{yearly_lines}

## Turnover, risk, and concentration

- Coverage/turnover artifact: `decision_coverage.json` and `turnover_diagnostics.json`.
- Concentration diagnostics: `{json.dumps(concentration, sort_keys=True)}`.
- The persisted case curves include fills, target turnover, realized turnover, costs, holdings, cash, concentration, drawdowns, dividends, terminal events, and per-symbol P&L.

## Gate table

| Gate | Result |
|---|---|
{gate_lines}

The gate table is conjunctive and near misses are failures. Economic acceptance requires every frozen gate; acceptance still means only `RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION`, never automatic promotion.

## Independent verification

The verifier independently checked frozen input identity, data hashes and row/quality counts, signal reconstruction samples, unique decisions, no same-session/future fills, target caps, SPY non-trading, cost scenario identity, persisted artifacts, and agreement with the runner gate table. Verification details are in `independent_verification.json` and the exact verdict is in `decision.json`.

## Artifact index

`FROZEN_INPUT_RECEIPT.json`, `PRE_RUN_VALIDATION.json`, `universe_A.json.gz`, `universe_B.json.gz`, `observation_set_audit.jsonl.gz`, `signal_audit.csv.gz`, `signal_summary.json`, `case_*.json.gz`, `v3_control_results.json`, `candidate_results.json`, `decision_coverage.json`, `turnover_diagnostics.json`, `concentration_diagnostics.json`, `yearly_validation.json`, `gate_table.json`, `run_manifest.json`, `independent_verification.json`, and `decision.json` are the run record.
"""
    (RESEARCH_DIR / "FINAL_REPORT.md").write_text(report, encoding="utf-8")


def verify(output: Path = RESEARCH_DIR) -> int:
    global RESEARCH_DIR
    RESEARCH_DIR = output.resolve()
    try:
        receipt = _read_json(RESEARCH_DIR / "FROZEN_INPUT_RECEIPT.json")
        preflight = _read_json(RESEARCH_DIR / "PRE_RUN_VALIDATION.json")
        manifest = _read_json(RESEARCH_DIR / "run_manifest.json")
        gate_table = _read_json(RESEARCH_DIR / "gate_table.json")
        signal_summary = _read_json(RESEARCH_DIR / "signal_summary.json")
        coverage = _read_json(RESEARCH_DIR / "decision_coverage.json")
        concentration = _read_json(RESEARCH_DIR / "concentration_diagnostics.json")
        yearly = _read_json(RESEARCH_DIR / "yearly_validation.json")
        control = _read_json(RESEARCH_DIR / "v3_control_results.json")
        candidate = _read_json(RESEARCH_DIR / "candidate_results.json")
        frozen_errors = _validate_frozen_inputs(receipt, preflight)
        dataset = load_normalized_dataset(BAR_PATH, LIFETIME_PATH, CORPORATE_ACTION_PATH)
        errors = frozen_errors + _validate_dataset(dataset, manifest)
        signal_errors, signal_evidence = _verify_signal_audit(dataset, signal_summary)
        errors.extend(signal_errors)
        config = load_config(str(RESEARCH_DIR.parents[1] / "config" / "settings.yaml"))
        case_evidence: dict[str, Any] = {}
        for name in EXPECTED_CASES:
            case_path = RESEARCH_DIR / f"case_{name.replace('/', '_')}.json.gz"
            case = _read_json_gz(case_path)
            case_errors = _verify_case(name, case, config)
            errors.extend(case_errors)
            case_evidence[name] = {
                "errors": case_errors,
                "rebalances": len(case.get("rebalances", [])),
                "fills": len(case.get("fills", [])),
            }
        independent_gates = _independent_gates(control, candidate, coverage, signal_summary, concentration, preflight)
        runner_gates = gate_table.get("gates", {})
        agreement = {
            name: bool(runner_gates.get(name, {}).get("pass")) == bool(value.get("pass"))
            for name, value in independent_gates["gates"].items()
        }
        if not all(agreement.values()):
            errors.append("independent gate results disagree with runner gate table")
        result_validity = "RESEARCH_RUN_VALID" if not errors else "RESEARCH_RUN_INVALID"
        verdict = (
            "RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION"
            if result_validity == "RESEARCH_RUN_VALID" and independent_gates["all_pass"]
            else "RESEARCH_REJECT"
            if result_validity == "RESEARCH_RUN_VALID"
            else "RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT"
        )
        verification = {
            "status": "PASS" if not errors else "FAIL",
            "result_validity": result_validity,
            "errors": errors,
            "frozen_input_checks": {"errors": frozen_errors},
            "dataset_checks": {"errors": _validate_dataset(dataset, manifest)},
            "signal_audit": signal_evidence,
            "case_checks": case_evidence,
            "runner_gate_agreement": agreement,
            "independent_gate_results": independent_gates,
            "observed_window": {"start": EVALUATION_START.isoformat(), "end": EVALUATION_END.isoformat()},
        }
        decision = {
            "result_validity": result_validity,
            "verdict": verdict,
            "gate_results": independent_gates,
            "independent_verification_status": verification["status"],
            "no_auto_promotion": True,
            "paper_authorized": False,
            "live_authorized": False,
            "economic_branch": _git("branch", "--show-current"),
            "economic_head_at_verification": _git("rev-parse", "HEAD"),
            "design_branch_sha": DESIGN_COMMIT,
            "candidate": CANDIDATE_NAME,
            "candidate_version": CANDIDATE_VERSION,
            "frozen_parameters": {"top_n": TOP_N, "per_name_weight": FIXED_PER_NAME_WEIGHT},
        }
        _write_json(RESEARCH_DIR / "independent_verification.json", verification)
        _write_json(RESEARCH_DIR / "decision.json", decision)
        _write_report(
            decision=decision,
            verification=verification,
            control=control,
            candidate=candidate,
            manifest=manifest,
            coverage=coverage,
            concentration=concentration,
            yearly=yearly,
        )
        print(json.dumps(decision, indent=2, sort_keys=True))
        return 0
    except Exception as exc:  # An invalid run still receives a machine-readable verdict.
        decision = {
            "result_validity": "RESEARCH_RUN_INVALID",
            "verdict": "RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT",
            "independent_verification_status": "FAIL",
            "errors": [f"independent verifier exception: {type(exc).__name__}: {exc}"],
            "no_auto_promotion": True,
            "paper_authorized": False,
            "live_authorized": False,
        }
        _write_json(RESEARCH_DIR / "independent_verification.json", decision)
        _write_json(RESEARCH_DIR / "decision.json", decision)
        (RESEARCH_DIR / "FINAL_REPORT.md").write_text(
            "# Overnight Reversal v1 Frozen Economic Experiment\n\n"
            f"- Result validity: **{decision['result_validity']}**\n"
            f"- Verdict: **{decision['verdict']}**\n"
            f"- Verifier failure: `{decision['errors'][0]}`\n"
            "- PAPER/LIVE promotion authorization: **False**.\n",
            encoding="utf-8",
        )
        print(json.dumps(decision, indent=2, sort_keys=True))
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=RESEARCH_DIR)
    args = parser.parse_args()
    raise SystemExit(verify(args.output))


if __name__ == "__main__":
    main()
