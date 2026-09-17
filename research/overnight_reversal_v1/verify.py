"""Independent, read-only verifier for the frozen overnight-reversal run."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import subprocess
from bisect import bisect_right
from datetime import date
from pathlib import Path
from statistics import stdev
from typing import Any

from parayabuck.backtest.regulatory_fees import rates_for_trade_date
from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot, hash_point_in_time_universe
from parayabuck.config import load_config
from parayabuck.core.models import Market
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
PERIOD_WINDOWS = {
    "development": (date(2017, 1, 3), date(2021, 12, 31)),
    "validation": (date(2022, 1, 3), date(2026, 7, 30)),
    "2022": (date(2022, 1, 3), date(2022, 12, 30)),
    "2023": (date(2023, 1, 3), date(2023, 12, 29)),
    "2024": (date(2024, 1, 2), date(2024, 12, 31)),
    "2025": (date(2025, 1, 2), date(2025, 12, 31)),
    "2026_partial": (date(2026, 1, 2), date(2026, 7, 30)),
    "full": (date(2017, 1, 3), date(2026, 7, 30)),
}


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


def _close(left: float, right: float, *, rel_tol: float = 1e-9, abs_tol: float = 1e-9) -> bool:
    return math.isclose(float(left), float(right), rel_tol=rel_tol, abs_tol=abs_tol)


def _optional_float(raw: object) -> float | None:
    text = str(raw or "").strip()
    return float(text) if text else None


def _csv_bool(raw: object) -> bool:
    return str(raw).strip().lower() == "true"


def _load_persisted_universe(path: Path) -> PointInTimeUniverse:
    raw = _read_json_gz(path)
    snapshots_raw = raw.get("snapshots")
    if not isinstance(snapshots_raw, list):
        raise ValueError(f"Persisted universe snapshots are malformed: {path}")
    snapshots: list[UniverseSnapshot] = []
    for item in snapshots_raw:
        if not isinstance(item, dict):
            raise ValueError(f"Persisted universe snapshot is malformed: {path}")
        snapshots.append(
            UniverseSnapshot(
                effective_at=_parse_timestamp(str(item["effective_at"])),
                symbols=frozenset(str(symbol) for symbol in item.get("symbols", [])),
                scanner_version=str(item.get("scanner_version", "")),
                config_digest=str(item.get("config_digest", "")),
                candidate_count=int(item.get("candidate_count", 0)),
                score_breakdown=dict(item.get("score_breakdown", {})),
                eliminated=dict(item.get("eliminated", {})),
                sector_metadata_coverage=float(item.get("sector_metadata_coverage", 1.0)),
                observation_set=tuple(str(symbol) for symbol in item.get("observation_set", [])),
                maximum_stream_symbols=int(item.get("maximum_stream_symbols", 0)),
                pinned_symbols=tuple(str(symbol) for symbol in item.get("pinned_symbols", [])),
                observation_strategy_name=str(item.get("observation_strategy_name", "")),
                observation_strategy_version=str(item.get("observation_strategy_version", "")),
            )
        )
    return PointInTimeUniverse(Market.EQUITY, tuple(snapshots), str(raw.get("methodology", "")))


def _verify_universes(
    *,
    config: Any,
    dataset: Any,
    manifest: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    """Re-read, rebuild, and compare both PIT universe snapshots."""
    errors: list[str] = []
    persisted_a = _load_persisted_universe(RESEARCH_DIR / "universe_A.json.gz")
    persisted_b = _load_persisted_universe(RESEARCH_DIR / "universe_B.json.gz")
    expected_identity = manifest.get("universe", {})
    if not isinstance(expected_identity, dict):
        errors.append("universe identity is malformed")
        expected_identity = {}
    persisted_hashes = {
        "A": hash_point_in_time_universe(persisted_a),
        "B": hash_point_in_time_universe(persisted_b),
    }
    if expected_identity.get("control_A_universe_hash") != persisted_hashes["A"]:
        errors.append("persisted control universe hash mismatch")
    if expected_identity.get("candidate_B_universe_hash") != persisted_hashes["B"]:
        errors.append("persisted candidate universe hash mismatch")
    if len(persisted_a.snapshots) != len(persisted_b.snapshots):
        errors.append("control and candidate persisted snapshot counts differ")
    for index, (left, right) in enumerate(zip(persisted_a.snapshots, persisted_b.snapshots, strict=False)):
        if left.effective_at != right.effective_at or left.symbols != right.symbols:
            errors.append(f"control/candidate eligible pool differs at snapshot {index}")
        if len(set(left.observation_set)) != len(left.observation_set):
            errors.append(f"control observation set contains duplicates at snapshot {index}")
        if len(set(right.observation_set)) != len(right.observation_set):
            errors.append(f"candidate observation set contains duplicates at snapshot {index}")
        if not set(left.observation_set).issubset(left.symbols):
            errors.append(f"control observation set escapes eligible pool at snapshot {index}")
        if not set(right.observation_set).issubset(right.symbols):
            errors.append(f"candidate observation set escapes eligible pool at snapshot {index}")
        if "SPY" not in right.observation_set:
            errors.append(f"candidate observation set does not pin SPY at snapshot {index}")
        if right.observation_strategy_name != CANDIDATE_NAME:
            errors.append(f"candidate observation strategy mismatch at snapshot {index}")
        if left.observation_strategy_name != config.strategy.equity.name:
            errors.append(f"control observation strategy mismatch at snapshot {index}")
        for symbol in right.symbols:
            lifetime = dataset.lifetimes.get(symbol)
            symbol_bars = dataset.bars_by_symbol.get(symbol, ())
            # A snapshot becomes effective at the next decision/open boundary.
            # A delisted symbol can therefore be present because its final
            # lifecycle-valid bar is the immediately preceding session (GGP is
            # an example).  Requiring valid_at(effective_at) would incorrectly
            # reject that legitimate historical membership.  The normalized
            # loader already checks lifecycle validity for every persisted bar;
            # here require a lifetime record and at least one historical bar
            # strictly before the snapshot, while the independent rebuild below
            # verifies the complete PIT membership and ranking.
            if lifetime is None:
                errors.append(f"candidate PIT lifetime record missing at snapshot {index}: {symbol}")
            elif not symbol_bars or symbol_bars[0].timestamp >= right.effective_at:
                errors.append(f"candidate PIT universe has no historical bar at snapshot {index}: {symbol}")
    audit_path = RESEARCH_DIR / "observation_set_audit.jsonl.gz"
    audit_rows = 0
    with gzip.open(audit_path, "rt", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            raw = json.loads(line)
            if not isinstance(raw, dict) or index >= len(persisted_b.snapshots):
                errors.append(f"observation-set audit row is malformed at {index}")
                continue
            audit_rows += 1
            left = persisted_a.snapshots[index]
            right = persisted_b.snapshots[index]
            if raw.get("effective_at") != left.effective_at.isoformat():
                errors.append(f"observation audit timestamp mismatch at {index}")
            expected_pool_hash = hashlib.sha256("|".join(sorted(left.symbols)).encode()).hexdigest()
            if raw.get("eligible_pool_hash") != expected_pool_hash:
                errors.append(f"observation audit eligible-pool hash mismatch at {index}")
            if tuple(raw.get("control_A_observation_set", ())) != left.observation_set:
                errors.append(f"control observation audit mismatch at {index}")
            if tuple(raw.get("candidate_B_observation_set", ())) != right.observation_set:
                errors.append(f"candidate observation audit mismatch at {index}")
    if audit_rows != len(persisted_b.snapshots):
        errors.append("observation-set audit row count mismatch")

    rebuilt_hashes: dict[str, str] = {}
    try:
        from parayabuck.backtest.product import build_product_universe
        from parayabuck.portfolio import portfolio_strategy_from
        from research.overnight_reversal_v1.candidate import OvernightReversalStrategy

        control_strategy = portfolio_strategy_from(
            config.strategy.equity.name,
            config.strategy.equity.parameters,
            timeframe="1d",
        )
        candidate_strategy = OvernightReversalStrategy()
        common = {
            "config": config,
            "bars": dataset.bars,
            "market": Market.EQUITY,
            "end": EVALUATION_END,
            "timeframe": "1d",
        }
        rebuilt_a = build_product_universe(**common, observation_strategy=control_strategy)
        rebuilt_b = build_product_universe(**common, observation_strategy=candidate_strategy)
        rebuilt_hashes = {
            "A": hash_point_in_time_universe(rebuilt_a),
            "B": hash_point_in_time_universe(rebuilt_b),
        }
        if rebuilt_hashes["A"] != persisted_hashes["A"] or rebuilt_hashes["B"] != persisted_hashes["B"]:
            errors.append("persisted PIT universe does not match an independent rebuild")
    except Exception as exc:
        errors.append(f"independent PIT universe rebuild failed: {type(exc).__name__}: {exc}")
    return errors, {
        "persisted_snapshot_count": len(persisted_b.snapshots),
        "observation_audit_rows": audit_rows,
        "persisted_hashes": persisted_hashes,
        "rebuilt_hashes": rebuilt_hashes,
        "eligible_pool_parity": not any("eligible pool differs" in error for error in errors),
        "candidate_observation_strategy": CANDIDATE_NAME,
        "SPY_reference_pinned": all("SPY" in snapshot.observation_set for snapshot in persisted_b.snapshots),
    }


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


def _independent_signal_fields(dataset: Any, symbol: str, decision_day: date, prior_day: date | None) -> dict[str, Any]:
    """Reconstruct the frozen signal without calling the candidate ranker."""
    fields: dict[str, Any] = {
        "prior_date": prior_day.isoformat() if prior_day is not None else None,
        "prior_close": None,
        "current_open": None,
        "stock_overnight": None,
        "spy_overnight": None,
        "spy_excess": None,
        "score": None,
        "eligibility": "ineligible",
        "reason": "no_prior_spy_session" if prior_day is None else "",
    }
    if prior_day is None:
        return fields
    spy_current = dataset.bar_for("SPY", decision_day)
    spy_prior = dataset.bar_for("SPY", prior_day)
    if spy_current is None or spy_prior is None:
        fields["reason"] = "missing_spy_reference"
        return fields
    current = dataset.bar_for(symbol, decision_day)
    prior = dataset.bar_for(symbol, prior_day)
    if current is None:
        fields["prior_close"] = prior.close if prior is not None else None
        fields["reason"] = "missing_provider_observation"
        return fields
    if prior is None:
        fields["current_open"] = current.open
        fields["reason"] = "session_gap"
        return fields
    stock_overnight = current.open / prior.close - 1.0
    spy_overnight = spy_current.open / spy_prior.close - 1.0
    excess = stock_overnight - spy_overnight
    fields.update(
        {
            "prior_close": prior.close,
            "current_open": current.open,
            "stock_overnight": stock_overnight,
            "spy_overnight": spy_overnight,
            "spy_excess": excess,
            "score": -excess,
            "eligibility": "eligible",
            "reason": "valid_common_session_pair",
        }
    )
    return fields


def _verify_signal_audit(
    dataset: Any,
    summary: dict[str, Any],
    candidate_universe: PointInTimeUniverse,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    path = RESEARCH_DIR / "signal_audit.csv.gz"
    rows_seen = 0
    valid_pairs = 0
    selected_rows = 0
    decision_symbols: set[tuple[str, str]] = set()
    sampled = 0
    decision_groups = 0
    boundary_groups = 0
    previous_decision_day: date | None = None
    current_timestamp: str | None = None
    current_group: list[dict[str, str]] = []
    snapshot_by_timestamp = {snapshot.effective_at.isoformat(): snapshot for snapshot in candidate_universe.snapshots}

    def verify_group(decision_timestamp: str, rows: list[dict[str, str]]) -> None:
        nonlocal decision_groups, boundary_groups, selected_rows, previous_decision_day
        if not rows:
            return
        decision_groups += 1
        try:
            timestamp = _parse_timestamp(decision_timestamp)
            decision_day = session_date(timestamp)
        except (TypeError, ValueError):
            errors.append(f"invalid signal decision timestamp: {decision_timestamp}")
            return
        if previous_decision_day is None or (decision_day - previous_decision_day).days > 3:
            boundary_groups += 1
        previous_decision_day = decision_day
        snapshot = snapshot_by_timestamp.get(decision_timestamp)
        if snapshot is None:
            errors.append(f"signal decision has no PIT snapshot: {decision_timestamp}")
            return
        prior_day = dataset.previous_spy_date(decision_day)
        observation = set(snapshot.observation_set)
        complete = all(dataset.bar_for(symbol, decision_day) is not None for symbol in observation)
        valid_rows: list[tuple[str, float, dict[str, str]]] = []
        observed_valid_rows: list[tuple[str, float, dict[str, str]]] = []
        for row in rows:
            symbol = str(row.get("symbol", ""))
            is_reference = symbol == "SPY"
            is_observed = symbol in observation
            if row.get("decision_timestamp") != decision_timestamp:
                errors.append(f"decision timestamp changed inside signal group: {decision_timestamp}")
            if row.get("decision_date") != decision_day.isoformat():
                errors.append(f"decision date mismatch at {decision_timestamp}/{symbol}")
            if row.get("prior_date") != (prior_day.isoformat() if prior_day is not None else ""):
                errors.append(f"prior common session mismatch at {decision_timestamp}/{symbol}")
            expected = (
                _independent_signal_fields(dataset, "SPY", decision_day, prior_day)
                if is_reference
                else _independent_signal_fields(dataset, symbol, decision_day, prior_day)
                if is_observed
                else {
                    "prior_date": prior_day.isoformat() if prior_day is not None else None,
                    "prior_close": None,
                    "current_open": None,
                    "stock_overnight": None,
                    "spy_overnight": None,
                    "spy_excess": None,
                    "score": None,
                    "eligibility": "ineligible",
                    "reason": "outside_observation_context",
                }
            )
            if is_reference:
                expected["eligibility"] = "reference_only"
                expected["reason"] = "reference_only"
            for field in (
                "prior_close",
                "current_open",
                "stock_overnight",
                "spy_overnight",
                "spy_excess",
                "score",
            ):
                actual = _optional_float(row.get(field))
                expected_value = expected[field]
                if expected_value is None:
                    if actual is not None:
                        errors.append(f"unexpected signal value for {field} at row {rows_seen}")
                elif actual is None or not _close(actual, float(expected_value), rel_tol=1e-10, abs_tol=1e-12):
                    errors.append(f"signal reconstruction mismatch for {field} at {decision_timestamp}/{symbol}")
            if row.get("eligibility") != expected["eligibility"] or row.get("reason") != expected["reason"]:
                errors.append(f"signal eligibility/reason mismatch at {decision_timestamp}/{symbol}")
            current = (
                dataset.bar_for(symbol, decision_day) if not is_reference else dataset.bar_for("SPY", decision_day)
            )
            if current is not None and current.timestamp > timestamp:
                errors.append(f"signal uses a future current bar at {decision_timestamp}/{symbol}")
            if is_reference:
                if _csv_bool(row.get("selected")):
                    errors.append(f"SPY is selected as a candidate target at {decision_timestamp}")
            elif expected["score"] is not None:
                value = float(expected["score"])
                valid_rows.append((symbol, value, row))
                if is_observed:
                    observed_valid_rows.append((symbol, value, row))
            if _csv_bool(row.get("selected")):
                selected_rows += 1
            if not is_reference and expected["score"] is not None:
                rank_raw = str(row.get("rank") or "").strip()
                if not rank_raw:
                    errors.append(f"eligible signal is missing rank at {decision_timestamp}/{symbol}")
        valid_rows.sort(key=lambda item: (-item[1], item[0]))
        observed_valid_rows.sort(key=lambda item: (-item[1], item[0]))
        rank_map = {symbol: index for index, (symbol, _score, _row) in enumerate(valid_rows, start=1)}
        observation_rank_map = {
            symbol: index for index, (symbol, _score, _row) in enumerate(observed_valid_rows, start=1)
        }
        for row in rows:
            symbol = str(row.get("symbol", ""))
            if symbol == "SPY":
                expected_member = symbol in observation
                expected_selected = False
                expected_reason = "reference_only"
            else:
                expected_member = symbol in observation
                expected_rank = observation_rank_map.get(symbol)
                expected_selected = bool(
                    complete and expected_member and expected_rank is not None and expected_rank <= TOP_N
                )
                expected_reason = (
                    "outside_observation_set"
                    if not expected_member
                    else "decision_incomplete_observation_set"
                    if not complete
                    else "top_n6"
                    if expected_selected
                    else "not_selected_rank"
                    if expected_rank is not None
                    else str(row.get("reason"))
                )
            rank_raw = str(row.get("rank") or "").strip()
            expected_rank = rank_map.get(symbol)
            if expected_rank is None:
                if rank_raw:
                    errors.append(f"ineligible signal has rank at {decision_timestamp}/{symbol}")
            elif rank_raw != str(expected_rank):
                errors.append(f"eligible-pool rank mismatch at {decision_timestamp}/{symbol}")
            observation_rank_raw = str(row.get("observation_rank") or "").strip()
            expected_observation_rank = observation_rank_map.get(symbol)
            if expected_observation_rank is None:
                if observation_rank_raw:
                    errors.append(f"unobserved signal has observation rank at {decision_timestamp}/{symbol}")
            elif observation_rank_raw != str(expected_observation_rank):
                errors.append(f"observation rank mismatch at {decision_timestamp}/{symbol}")
            if _csv_bool(row.get("observation_member")) != expected_member:
                errors.append(f"observation membership mismatch at {decision_timestamp}/{symbol}")
            if _csv_bool(row.get("decision_complete")) != complete:
                errors.append(f"decision completeness mismatch at {decision_timestamp}/{symbol}")
            if _csv_bool(row.get("selected")) != expected_selected:
                errors.append(f"selection mismatch at {decision_timestamp}/{symbol}")
            if row.get("selected_reason") != expected_reason:
                errors.append(f"selection reason mismatch at {decision_timestamp}/{symbol}")

    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows_seen += 1
            decision_timestamp = str(row.get("decision_timestamp", ""))
            if current_timestamp is None:
                current_timestamp = decision_timestamp
            elif decision_timestamp != current_timestamp:
                verify_group(current_timestamp, current_group)
                current_timestamp = decision_timestamp
                current_group = []
            current_group.append(row)
            decision_key = (decision_timestamp, str(row.get("symbol", "")))
            if decision_key in decision_symbols:
                errors.append(f"duplicate signal audit key: {decision_key}")
            decision_symbols.add(decision_key)
            if rows_seen <= 3 or rows_seen % 1000 == 0:
                sampled += 1
            if str(row.get("symbol")) != "SPY" and _optional_float(row.get("score")) is not None:
                valid_pairs += 1
    if current_timestamp is not None:
        verify_group(current_timestamp, current_group)
        if rows_seen > 3 and rows_seen % 1000 != 0:
            sampled += 1
    expected_rows = int(summary.get("pool_row_count", -1))
    expected_valid = int(summary.get("valid_common_pair_count", -1))
    if rows_seen != expected_rows:
        errors.append("signal audit row count disagrees with signal summary")
    if valid_pairs != expected_valid:
        errors.append("signal audit valid-pair count disagrees with signal summary")
    if decision_groups != int(summary.get("decision_count", -1)):
        errors.append("signal audit decision count disagrees with signal summary")
    if selected_rows != int(summary.get("selected_row_count", -1)):
        errors.append("signal audit selected-row count disagrees with signal summary")
    return errors, {
        "rows_seen": rows_seen,
        "valid_pairs_seen": valid_pairs,
        "sampled_rows": sampled,
        "reconstructed_rows": rows_seen,
        "decision_groups": decision_groups,
        "boundary_decision_groups": boundary_groups,
        "duplicate_keys": len(decision_symbols) != rows_seen,
        "coverage_mode": "full_population_plus_boundary_checks",
        "score_reconstruction": "PASS" if not errors else "FAIL",
    }


def _expected_equity_fee(
    side: str, quantity: float, price: float, trade_date: date, multiplier: float, config: Any
) -> float:
    rates = rates_for_trade_date(trade_date)
    cat: float = quantity * float(rates.cat_per_share)
    regulatory = cat
    if side == "sell":
        regulatory += min(quantity * float(rates.taf_per_share), float(rates.taf_cap)) + quantity * price * float(
            rates.sec_fee_rate
        )
    elif side != "buy":
        raise ValueError(f"unknown equity order side: {side}")
    # The research fee shim scales the effective-dated regulatory schedule;
    # commission is separate and remains the configured zero value here.
    commission: float = float(str(config.backtest.commission))
    return float(commission + multiplier * regulatory)


def _verify_costs(name: str, case: dict[str, Any], config: Any) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    multiplier = float(case.get("cost_multiplier", 0.0))
    slippage_bps = float(config.backtest.slippage_bps) * multiplier
    spread_bps = float(config.backtest.spread_bps) * multiplier
    totals = {"slippage": 0.0, "spread": 0.0, "fees": 0.0}
    fills = case.get("fills", [])
    if not isinstance(fills, list):
        return [f"{name}: fills are malformed for cost verification"], {}
    for index, raw in enumerate(fills):
        try:
            side = str(raw["side"])
            quantity = float(raw["qty"])
            reference = float(raw["reference_price"])
            fill_price = float(raw["fill_price"])
            timestamp = _parse_timestamp(str(raw["timestamp"]))
            adverse = slippage_bps + spread_bps / 2.0
            direction = 1.0 if side == "buy" else -1.0 if side == "sell" else 0.0
            expected_fill = reference * (1.0 + direction * adverse / 10_000.0)
            expected_slippage = quantity * reference * slippage_bps / 10_000.0
            expected_spread = quantity * reference * (spread_bps / 2.0) / 10_000.0
            expected_fee = _expected_equity_fee(side, quantity, fill_price, timestamp.date(), multiplier, config)
            expected = {
                "fill_price": expected_fill,
                "slippage_cost": expected_slippage,
                "spread_cost": expected_spread,
                "fee": expected_fee,
            }
            for field, value in expected.items():
                if not _close(float(raw[field]), value, rel_tol=1e-8, abs_tol=1e-9):
                    errors.append(f"{name}: cost formula mismatch for {field} at fill {index}")
            totals["slippage"] += float(raw["slippage_cost"])
            totals["spread"] += float(raw["spread_cost"])
            totals["fees"] += float(raw["fee"])
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{name}: malformed fill cost record {index}: {exc}")
    terminal = case.get("terminal_events", [])
    terminal_count = 0
    if not isinstance(terminal, list):
        errors.append(f"{name}: terminal events are malformed for cost verification")
        terminal = []
    for index, raw in enumerate(terminal):
        try:
            quantity = float(raw["quantity"])
            last_close = float(raw["last_close"])
            fill_price = float(raw["fill_price"])
            timestamp = _parse_timestamp(str(raw["timestamp"]))
            adverse = slippage_bps + spread_bps / 2.0
            expected = {
                "fill_price": last_close * (1.0 - adverse / 10_000.0),
                "slippage_cost": quantity * last_close * slippage_bps / 10_000.0,
                "spread_cost": quantity * last_close * (spread_bps / 2.0) / 10_000.0,
                "fee": _expected_equity_fee("sell", quantity, fill_price, timestamp.date(), multiplier, config),
            }
            for field, value in expected.items():
                if not _close(float(raw[field]), value, rel_tol=1e-8, abs_tol=1e-9):
                    errors.append(f"{name}: terminal cost formula mismatch for {field} at event {index}")
            totals["slippage"] += float(raw["slippage_cost"])
            totals["spread"] += float(raw["spread_cost"])
            totals["fees"] += float(raw["fee"])
            terminal_count += 1
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"{name}: malformed terminal cost record {index}: {exc}")
    persisted_totals = case.get("cost_totals", {})
    if not isinstance(persisted_totals, dict):
        errors.append(f"{name}: cost_totals is malformed")
        persisted_totals = {}
    for key in totals:
        if not _close(float(persisted_totals.get(key, math.nan)), totals[key], rel_tol=1e-8, abs_tol=1e-8):
            errors.append(f"{name}: persisted {key} total disagrees with fills/events")
    transaction_total = sum(totals.values())
    if not _close(
        float(persisted_totals.get("transaction_costs", math.nan)), transaction_total, rel_tol=1e-8, abs_tol=1e-8
    ):
        errors.append(f"{name}: persisted transaction-cost total disagrees with components")
    summary = case.get("summary", {})
    if isinstance(summary, dict):
        for key, field in (("slippage", "slippage_cost_total"), ("spread", "spread_cost_total"), ("fees", "fee_total")):
            if not _close(float(summary.get(field, math.nan)), totals[key], rel_tol=1e-8, abs_tol=1e-8):
                errors.append(f"{name}: report {field} disagrees with independent cost total")
    return errors, {
        "fill_count": len(fills),
        "terminal_event_count": terminal_count,
        "slippage_bps": slippage_bps,
        "spread_bps": spread_bps,
        "fee_multiplier": multiplier,
        "recomputed_totals": totals | {"transaction_costs": transaction_total},
        "cost_formula_check": "PASS" if not errors else "FAIL",
    }


def _latest_close(dataset: Any, symbol: str, timestamp: Any) -> float | None:
    bars = dataset.bars_by_symbol.get(symbol, ())
    if not bars:
        return None
    index = bisect_right(bars, timestamp, key=lambda item: item.timestamp) - 1
    return float(bars[index].close) if index >= 0 else None


def _verify_account(name: str, case: dict[str, Any], dataset: Any, config: Any) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    raw_curve = case.get("equity_curve", [])
    raw_holdings = case.get("holdings_history", [])
    if not isinstance(raw_curve, list) or not isinstance(raw_holdings, list):
        return [f"{name}: account curve/holdings artifact is malformed"], {}
    points = [(_parse_timestamp(str(point["timestamp"])), float(point["equity"])) for point in raw_curve]
    if len(points) != len(raw_holdings):
        errors.append(f"{name}: curve and holdings history lengths differ")
    for previous, current in zip(points, points[1:]):
        if current[0] <= previous[0]:
            errors.append(f"{name}: account curve timestamps are not strictly increasing")
            break
    curve_timestamps = {timestamp for timestamp, _equity in points}
    fills_by_timestamp: dict[Any, list[dict[str, Any]]] = {}
    for raw in case.get("fills", []) if isinstance(case.get("fills", []), list) else []:
        timestamp = _parse_timestamp(str(raw["timestamp"]))
        if timestamp not in curve_timestamps:
            errors.append(f"{name}: fill timestamp is absent from account curve: {timestamp.isoformat()}")
        fills_by_timestamp.setdefault(timestamp, []).append(raw)
    terminal_by_timestamp: dict[Any, list[dict[str, Any]]] = {}
    for raw in case.get("terminal_events", []) if isinstance(case.get("terminal_events", []), list) else []:
        timestamp = _parse_timestamp(str(raw["timestamp"]))
        if timestamp not in curve_timestamps:
            errors.append(f"{name}: terminal timestamp is absent from account curve: {timestamp.isoformat()}")
        terminal_by_timestamp.setdefault(timestamp, []).append(raw)
    actions = list(dataset.corporate_actions)
    action_index = 0
    cash = float(case.get("starting_equity", 0.0))
    positions: dict[str, float] = {}
    dividend_total = 0.0
    max_gross = 0.0
    minimum_cash = cash
    deferred_final: list[dict[str, Any]] = []
    last_timestamp = points[-1][0] if points else None

    def apply_terminal(raw: dict[str, Any]) -> None:
        nonlocal cash
        symbol = str(raw["symbol"])
        quantity = float(raw["quantity"])
        held = positions.get(symbol, 0.0)
        if quantity > held + 1e-7:
            errors.append(f"{name}: terminal quantity exceeds held quantity for {symbol}")
        positions[symbol] = max(0.0, held - quantity)
        if positions[symbol] <= 1e-9:
            positions.pop(symbol, None)
        cash += quantity * float(raw["fill_price"]) - float(raw["fee"])

    for index, (timestamp, recorded_equity) in enumerate(points):
        while action_index < len(actions) and actions[action_index].timestamp <= timestamp:
            action = actions[action_index]
            held = positions.get(action.symbol, 0.0)
            if held > 0:
                cash += held * float(action.value)
                dividend_total += held * float(action.value)
            action_index += 1
        for raw in fills_by_timestamp.get(timestamp, []):
            symbol = str(raw["symbol"])
            quantity = float(raw["qty"])
            fill_value = quantity * float(raw["fill_price"])
            fee = float(raw["fee"])
            if str(raw["side"]) == "buy":
                cash -= fill_value + fee
                positions[symbol] = positions.get(symbol, 0.0) + quantity
            elif str(raw["side"]) == "sell":
                held = positions.get(symbol, 0.0)
                if quantity > held + 1e-7:
                    errors.append(f"{name}: sell quantity exceeds held quantity for {symbol}")
                positions[symbol] = max(0.0, held - quantity)
                if positions[symbol] <= 1e-9:
                    positions.pop(symbol, None)
                cash += fill_value - fee
            else:
                errors.append(f"{name}: unknown fill side in account ledger")
        regular_terminal = terminal_by_timestamp.get(timestamp, [])
        deferred_final = []
        if timestamp == last_timestamp:
            deferred_final = [raw for raw in regular_terminal if str(raw.get("policy")) == "FINAL_BAR"]
            regular_terminal = [raw for raw in regular_terminal if str(raw.get("policy")) != "FINAL_BAR"]
        for raw in regular_terminal:
            apply_terminal(raw)
        holdings_cash = cash
        holdings_positions = dict(positions)
        for raw in deferred_final:
            apply_terminal(raw)
        position_value = sum(
            quantity * float(price)
            for symbol, quantity in positions.items()
            if (price := _latest_close(dataset, symbol, timestamp)) is not None
        )
        equity = cash + position_value
        if cash < -1e-6:
            errors.append(f"{name}: reconstructed cash is negative at {timestamp.isoformat()}")
        minimum_cash = min(minimum_cash, cash)
        gross = position_value / equity if equity > 0 else math.inf
        max_gross = max(max_gross, gross)
        if gross > float(config.risk.max_gross_exposure_pct) + 1e-6:
            errors.append(f"{name}: reconstructed gross exposure exceeds configured cap at {timestamp.isoformat()}")
        if not _close(recorded_equity, equity, rel_tol=1e-8, abs_tol=1e-5):
            errors.append(f"{name}: NAV identity mismatch at {timestamp.isoformat()}")
        if index < len(raw_holdings):
            holding = raw_holdings[index]
            holding_position_value = sum(
                quantity * float(price)
                for symbol, quantity in holdings_positions.items()
                if (price := _latest_close(dataset, symbol, timestamp)) is not None
            )
            holding_equity = holdings_cash + holding_position_value
            expected_cash_ratio = max(0.0, holdings_cash / holding_equity) if holding_equity > 0 else 1.0
            expected_concentration = (
                max(
                    (
                        quantity * float(price) / holding_equity
                        for symbol, quantity in holdings_positions.items()
                        if (price := _latest_close(dataset, symbol, timestamp)) is not None
                    ),
                    default=0.0,
                )
                if holding_equity > 0
                else 0.0
            )
            if int(holding.get("positions", -1)) != len(holdings_positions):
                errors.append(f"{name}: holdings count mismatch at {timestamp.isoformat()}")
            if not _close(float(holding.get("cash_ratio", math.nan)), expected_cash_ratio, rel_tol=1e-8, abs_tol=1e-8):
                errors.append(f"{name}: cash ratio mismatch at {timestamp.isoformat()}")
            if not _close(
                float(holding.get("concentration", math.nan)), expected_concentration, rel_tol=1e-8, abs_tol=1e-8
            ):
                errors.append(f"{name}: concentration mismatch at {timestamp.isoformat()}")
    if points and not _close(float(case.get("ending_equity", math.nan)), points[-1][1], rel_tol=1e-10, abs_tol=1e-6):
        errors.append(f"{name}: top-level ending equity disagrees with curve")
    if not _close(float(case.get("dividend_total", math.nan)), dividend_total, rel_tol=1e-8, abs_tol=1e-6):
        errors.append(f"{name}: dividend total disagrees with corporate-action ledger")
    if positions:
        errors.append(f"{name}: reconstructed account is not flat after final liquidation")
    return errors, {
        "curve_points": len(points),
        "reconstructed_dividend_total": dividend_total,
        "minimum_cash": minimum_cash,
        "maximum_reconstructed_gross": max_gross,
        "final_positions": len(positions),
        "nav_identity": "PASS" if not errors else "FAIL",
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
        if float(record.get("target_cash_weight", -1.0)) > 1.0 + EPSILON:
            errors.append(f"{name}: target cash exceeds one")
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
        if len(target_symbols) != len(weights):
            errors.append(f"{name}: duplicate target symbol")
        if sum(float(pair[1]) for pair in weights) + float(record.get("target_cash_weight", 0.0)) > 1.0 + EPSILON:
            errors.append(f"{name}: target portfolio is levered")
        if arm == "B" and float(record.get("target_cash_weight", -1.0)) < 1.0 - 0.55 - EPSILON:
            errors.append(f"{name}: candidate cash below the unused-allocation floor")
    raw_targets = case.get("raw_targets", [])
    if arm == "B":
        if not isinstance(raw_targets, list) or len(raw_targets) != len(rebalances):
            errors.append(f"{name}: raw candidate target count does not match decisions")
        elif isinstance(raw_targets, list):
            for target in raw_targets:
                weights = target.get("weights", [])
                if not isinstance(weights, list) or len(weights) > TOP_N:
                    errors.append(f"{name}: raw candidate target exceeds top_n")
                    continue
                if float(target.get("cash_weight", -1.0)) < 1.0 - 0.55 - EPSILON:
                    errors.append(f"{name}: raw candidate target cash below unused allocation")
                if (
                    sum(float(item.get("weight", 0.0)) for item in weights) + float(target.get("cash_weight", 0.0))
                    > 1.0 + EPSILON
                ):
                    errors.append(f"{name}: raw candidate target is levered")
                for item in weights:
                    if str(item.get("symbol")) == "SPY" or not math.isclose(
                        float(item.get("weight", -1.0)), FIXED_PER_NAME_WEIGHT, rel_tol=0.0, abs_tol=1e-12
                    ):
                        errors.append(f"{name}: raw candidate target violates fixed six-name sizing")
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
        if str(fill.get("market")) != "equity":
            errors.append(f"{name}: non-equity fill in equity case")
        if str(fill.get("side")) not in {"buy", "sell"}:
            errors.append(f"{name}: invalid fill side")
        if str(fill["decision_timestamp"]) not in rebalance_timestamps:
            errors.append(f"{name}: fill references unknown decision")
        for field in ("qty", "reference_price", "fill_price", "slippage_cost", "spread_cost", "fee"):
            if not _finite(fill.get(field)) or float(fill.get(field)) < -EPSILON:
                errors.append(f"{name}: invalid fill field {field}")
        if (
            float(fill.get("qty", 0.0)) <= EPSILON
            or float(fill.get("reference_price", 0.0)) <= 0
            or float(fill.get("fill_price", 0.0)) <= 0
        ):
            errors.append(f"{name}: non-positive fill quantity or price")
    for point in case.get("equity_curve", []):
        if not _finite(point.get("equity")) or float(point["equity"]) <= 0:
            errors.append(f"{name}: invalid equity curve point")
    return errors


def _recompute_metrics_for_window(case: dict[str, Any], start_day: date, end_day: date) -> dict[str, Any]:
    """Recompute the campaign's gate metrics from persisted case artifacts."""
    raw_curve = case.get("equity_curve", [])
    if not isinstance(raw_curve, list) or not raw_curve:
        raise ValueError("case has no equity curve")
    points = [(_parse_timestamp(str(point["timestamp"])), float(point["equity"])) for point in raw_curve]
    prior = [(timestamp, equity) for timestamp, equity in points if session_date(timestamp) < start_day]
    in_window = [(timestamp, equity) for timestamp, equity in points if start_day <= session_date(timestamp) <= end_day]
    if not in_window:
        raise ValueError(f"case has no curve points in {start_day}..{end_day}")
    if prior:
        anchor = prior[-1]
    else:
        anchor = (_parse_timestamp(str(case["summary"]["start_date"])), float(case["starting_equity"]))
    series = [anchor] + [point for point in in_window if point[0] != anchor[0]]
    ending = series[-1][1]
    duration_days = max(1, int((series[-1][0] - series[0][0]).total_seconds() / 86_400))
    years = duration_days / 365.0
    total_return = ending / anchor[1] - 1.0 if anchor[1] > 0 else 0.0
    cagr = (ending / anchor[1]) ** (1 / years) - 1.0 if anchor[1] > 0 and ending > 0 else 0.0
    returns = [
        series[index][1] / series[index - 1][1] - 1.0 for index in range(1, len(series)) if series[index - 1][1] > 0
    ]
    volatility = stdev(returns) if len(returns) >= 2 else 0.0
    annualizer = math.sqrt(len(returns) / years) if returns and years > 0 else 1.0
    mean_return = sum(returns) / len(returns) if returns else 0.0
    downside = [value for value in returns if value < 0]
    downside_std = stdev(downside) if len(downside) >= 2 else 0.0
    peak = anchor[1]
    max_drawdown = 0.0
    for _timestamp, equity in series:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)

    costs = {"slippage": 0.0, "spread": 0.0, "fees": 0.0}
    for raw in case.get("fills", []) if isinstance(case.get("fills", []), list) else []:
        timestamp = _parse_timestamp(str(raw["timestamp"]))
        if start_day <= session_date(timestamp) <= end_day:
            costs["slippage"] += float(raw.get("slippage_cost", 0.0))
            costs["spread"] += float(raw.get("spread_cost", 0.0))
            costs["fees"] += float(raw.get("fee", 0.0))
    for raw in case.get("terminal_events", []) if isinstance(case.get("terminal_events", []), list) else []:
        timestamp = _parse_timestamp(str(raw["timestamp"]))
        if start_day <= session_date(timestamp) <= end_day:
            costs["slippage"] += float(raw.get("slippage_cost", 0.0))
            costs["spread"] += float(raw.get("spread_cost", 0.0))
            costs["fees"] += float(raw.get("fee", 0.0))
    transaction_costs = sum(costs.values())
    raw_rebalances = case.get("rebalances", [])
    rebalances = [
        raw
        for raw in raw_rebalances
        if isinstance(raw_rebalances, list)
        if start_day <= session_date(_parse_timestamp(str(raw["decision_timestamp"]))) <= end_day
    ]
    expected_turnover = sum(float(raw.get("expected_turnover", 0.0)) for raw in rebalances)
    fills = [
        raw
        for raw in case.get("fills", [])
        if isinstance(case.get("fills", []), list)
        if start_day <= session_date(_parse_timestamp(str(raw["timestamp"]))) <= end_day
    ]
    realized_turnover_notional = sum(abs(float(raw["qty"]) * float(raw["reference_price"])) for raw in fills)
    average_equity = sum(equity for _timestamp, equity in series) / len(series)
    realized_turnover = realized_turnover_notional / average_equity if average_equity > 0 else 0.0
    annualized_target_turnover = expected_turnover / years if years > 0 else 0.0
    annualized_realized_turnover = realized_turnover / years if years > 0 else 0.0
    monthly_ends: dict[tuple[int, int], float] = {}
    for timestamp, equity in series[1:]:
        monthly_ends[(timestamp.year, timestamp.month)] = equity
    monthly_returns: dict[tuple[int, int], float] = {}
    previous = anchor[1]
    for month_key in sorted(monthly_ends):
        monthly_returns[month_key] = monthly_ends[month_key] / previous - 1.0 if previous > 0 else 0.0
        previous = monthly_ends[month_key]
    month_values = list(monthly_returns.values())
    events = [
        raw
        for raw in case.get("realized_pnl_events", [])
        if isinstance(case.get("realized_pnl_events", []), list)
        and start_day <= session_date(_parse_timestamp(str(raw["timestamp"]))) <= end_day
    ]
    pnl_values = [float(raw["pnl"]) for raw in events]
    gross_profit = sum(value for value in pnl_values if value > 0)
    gross_loss = abs(sum(value for value in pnl_values if value < 0))
    holdings = [
        raw
        for raw in case.get("holdings_history", [])
        if isinstance(case.get("holdings_history", []), list)
        and start_day <= session_date(_parse_timestamp(str(raw["timestamp"]))) <= end_day
    ]
    average_cash = sum(float(raw["cash_ratio"]) for raw in holdings) / len(holdings) if holdings else 1.0
    average_positions = sum(int(raw["positions"]) for raw in holdings) / len(holdings) if holdings else 0.0
    max_concentration = max((float(raw["concentration"]) for raw in holdings), default=0.0)
    max_positions = max((int(raw["positions"]) for raw in holdings), default=0)
    max_target_weight = max(
        (float(weight) for raw in rebalances for _symbol, weight in raw.get("target_weights", ())),
        default=0.0,
    )
    benchmark_points = [
        (_parse_timestamp(str(raw["timestamp"])), float(raw["close"]))
        for raw in case.get("benchmark_bars", [])
        if isinstance(case.get("benchmark_bars", []), list)
    ]
    benchmark_prior = [
        (timestamp, close) for timestamp, close in benchmark_points if session_date(timestamp) < start_day
    ]
    benchmark_window = [
        (timestamp, close) for timestamp, close in benchmark_points if start_day <= session_date(timestamp) <= end_day
    ]
    benchmark_return = 0.0
    if benchmark_window:
        benchmark_anchor = benchmark_prior[-1][1] if benchmark_prior else benchmark_window[0][1]
        benchmark_return = benchmark_window[-1][1] / benchmark_anchor - 1.0 if benchmark_anchor > 0 else 0.0
    return {
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "starting_equity": anchor[1],
        "ending_equity": ending,
        "net_total_return": total_return,
        "net_return": total_return,
        "benchmark_return": benchmark_return,
        "cagr": cagr,
        "annualized_volatility": volatility * annualizer if volatility > 0 else 0.0,
        "sharpe": mean_return / volatility * annualizer if volatility > 0 else 0.0,
        "sortino": mean_return / downside_std * annualizer if downside_std > 0 else 0.0,
        "max_drawdown": max_drawdown,
        "calmar": cagr / max_drawdown if max_drawdown > 0 else 0.0,
        "worst_month_return": min(month_values, default=0.0),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "annualized_one_way_target_turnover": annualized_target_turnover,
        "annualized_one_way_realized_turnover": annualized_realized_turnover,
        "target_turnover": expected_turnover,
        "realized_turnover": realized_turnover,
        "gross_notional": realized_turnover_notional,
        "realized_transaction_costs": transaction_costs,
        "slippage_cost": costs["slippage"],
        "spread_cost": costs["spread"],
        "fee_cost": costs["fees"],
        "costs_pct_starting_capital": transaction_costs / anchor[1] if anchor[1] > 0 else 0.0,
        "costs_pct_gross_gain": transaction_costs / max(0.0, ending - anchor[1]) if ending > anchor[1] else None,
        "fill_count": len(fills),
        "rebalance_count": len(rebalances),
        "decision_count": len(rebalances),
        "average_exposure": max(0.0, 1.0 - average_cash),
        "average_positions": average_positions,
        "max_positions": max_positions,
        "max_concentration": max_concentration,
        "max_target_weight": max_target_weight,
        "positive_month_ratio": sum(value > 0 for value in month_values) / len(month_values) if month_values else 0.0,
        "duration_days": duration_days,
        "realized_pnl": sum(pnl_values),
        "dividend_total": float(case.get("dividend_total", 0.0)),
    }


def _verify_metrics(
    cases: dict[str, dict[str, Any]],
    control: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[list[str], dict[str, Any], dict[str, Any], dict[str, Any]]:
    errors: list[str] = []
    recomputed: dict[str, dict[str, Any]] = {}
    fields_checked = 0
    periods_checked = 0
    for name, case in cases.items():
        recomputed[name] = {"arm": name.split("/", 1)[0], "cost_scenario": name.split("/", 1)[1], "periods": {}}
        for period, (start_day, end_day) in PERIOD_WINDOWS.items():
            expected = _recompute_metrics_for_window(case, start_day, end_day)
            recomputed[name]["periods"][period] = expected
            periods_checked += 1
            source = control if name.startswith("A/") else candidate
            recorded_case = source.get(name, {})
            recorded_periods = recorded_case.get("periods", {}) if isinstance(recorded_case, dict) else {}
            recorded = recorded_periods.get(period, {}) if isinstance(recorded_periods, dict) else {}
            if not isinstance(recorded, dict):
                errors.append(f"{name}/{period}: persisted metric period is malformed")
                continue
            for key, expected_value in expected.items():
                fields_checked += 1
                if key not in recorded:
                    errors.append(f"{name}/{period}: missing metric {key}")
                    continue
                actual_value = recorded[key]
                if expected_value is None:
                    if actual_value is not None:
                        errors.append(f"{name}/{period}: metric {key} should be null")
                elif isinstance(expected_value, str):
                    if actual_value != expected_value:
                        errors.append(f"{name}/{period}: metric {key} mismatch")
                elif not _close(float(actual_value), float(expected_value), rel_tol=1e-8, abs_tol=1e-8):
                    errors.append(f"{name}/{period}: metric {key} mismatch")
    recomputed_control = {name: value for name, value in recomputed.items() if name.startswith("A/")}
    recomputed_candidate = {name: value for name, value in recomputed.items() if name.startswith("B/")}
    return (
        errors,
        {
            "cases_checked": len(cases),
            "periods_checked": periods_checked,
            "metric_fields_checked": fields_checked,
            "recomputed_from": "equity_curve, fills, terminal_events, rebalances, holdings_history, benchmark_bars",
            "status": "PASS" if not errors else "FAIL",
        },
        recomputed_control,
        recomputed_candidate,
    )


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
    control_lines = "\n\n".join(
        _summary_line(case_metrics, f"A/{cost}", period)
        for cost in ("1x", "2x", "3x")
        for period in ("development", "validation", "full")
    )
    candidate_lines = "\n\n".join(
        _summary_line(case_metrics, f"B/{cost}", period)
        for cost in ("1x", "2x", "3x")
        for period in ("development", "validation", "full")
    )
    yearly_lines = "\n".join(
        f"| {year} | {float(values['candidate_B_1x']['net_total_return']):.4%} | {float(values['control_A_1x']['net_total_return']):.4%} |"
        for year, values in yearly.items()
        if isinstance(values, dict) and "candidate_B_1x" in values
    )
    coverage_summary = {
        arm: {
            key: coverage.get(arm, {}).get(key)
            for key in (
                "scheduled_decisions",
                "actual_decisions",
                "skipped_decisions",
                "skipped_reasons",
                "first_decision",
                "last_decision",
                "longest_actual_decision_gap_days",
                "target_notional_coverage_ratio",
                "target_notional_within_two_ratio",
                "valid_overnight_cross_sections",
                "average_valid_eligible_names",
                "average_observation_set_size",
                "average_selected_names",
            )
        }
        for arm in ("A", "B")
    }
    concentration_summary = {
        key: concentration.get(key)
        for key in (
            "unique_symbols",
            "largest_absolute_realized_contributor",
            "largest_absolute_contributor_share",
            "leave_one_dominant_symbol_validation_cagr",
            "max_sector_count_in_targets",
            "target_max_position_weight",
        )
    }
    failed_gates = [name for name, value in gates.items() if not value.get("pass")]
    full_deltas = gates.get("G3", {}).get("evidence", {}).get("full_deltas", [])
    validation_deltas = gates.get("G3", {}).get("evidence", {}).get("validation_deltas", [])
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
- Decision coverage summary: `{json.dumps(coverage_summary, sort_keys=True)}`. Full fill-completion records remain in `decision_coverage.json`.

## Control A — V3

{control_lines}

## Candidate B — overnight reversal

{candidate_lines}

All three cost scenarios are present for development, validation, and full periods above; the complete metric tables are in `v3_control_results.json` and `candidate_results.json`.

## Risk-adjusted comparison with V3

- Candidate B minus Control A full-period 1x Sharpe/Sortino/Calmar deltas: `{full_deltas}`.
- Candidate B minus Control A validation 1x Sharpe/Sortino/Calmar deltas: `{validation_deltas}`.
- Candidate B 1x full-period maximum drawdown: `{float(_metric(case_metrics, "B/1x", "full")["max_drawdown"]):.4%}`; validation: `{float(_metric(case_metrics, "B/1x", "validation")["max_drawdown"]):.4%}`.

Cost cases `1x`, `2x`, and `3x` scale slippage, spread, and effective-dated equity regulatory fees; commission remains the configured zero value. Full case artifacts are `case_A_1x.json.gz`, `case_A_2x.json.gz`, `case_A_3x.json.gz`, `case_B_1x.json.gz`, `case_B_2x.json.gz`, and `case_B_3x.json.gz`.

## Yearly validation

| Year | Candidate B 1x total return | Control A 1x total return |
|---|---:|---:|
{yearly_lines}

## Turnover, risk, and concentration

- Coverage/turnover artifact: `decision_coverage.json` and `turnover_diagnostics.json`.
- Concentration summary: `{json.dumps(concentration_summary, sort_keys=True)}`. The full symbol-level contribution map is in `concentration_diagnostics.json`.
- The persisted case curves include fills, target turnover, realized turnover, costs, holdings, cash, concentration, drawdowns, dividends, terminal events, and per-symbol P&L.

## Gate table

| Gate | Result |
|---|---|
{gate_lines}

The gate table is conjunctive and near misses are failures. Economic acceptance requires every frozen gate; acceptance still means only `RESEARCH_ACCEPT_FOR_FORWARD_PAPER_EVALUATION`, never automatic promotion.

## Independent verification

The verifier independently checked frozen input identity, data hashes and row/quality counts, rebuilt both PIT universes, reconstructed the full persisted signal-audit population plus boundary groups, checked unique daily decisions, no same-session/future fills, target caps, SPY non-trading, every fill and terminal cost formula, the account cash/NAV ledger, all six cases and all non-warmup metric periods, and agreement with the runner gate table. Verification details, check count, and failure count are in `independent_verification.json`; the exact verdict is in `decision.json`.

## Final reasoning

The run is valid, but the frozen conjunctive economic gates failed: `{", ".join(failed_gates)}`. No near miss was promoted to a pass and no replacement candidate or post-result rescue was attempted.

## Files created

The complete run record is the focused artifact set under `research/overnight_reversal_v1/`, including the pre-run receipt, implementation, tests, signal and observation audits, six compressed case files, metric/cost/coverage/concentration diagnostics, gate table, independent verification, decision, and this report.

## Economic research branch final SHA

Verification head: `{manifest.get("economic_head_at_verification") or decision.get("economic_head_at_verification")}`. The final committed branch SHA is reported with the handoff after the artifact commit.

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
        dataset_errors = _validate_dataset(dataset, manifest)
        config = load_config(str(RESEARCH_DIR.parents[1] / "config" / "settings.yaml"))
        universe_errors, universe_evidence = _verify_universes(config=config, dataset=dataset, manifest=manifest)
        candidate_universe = _load_persisted_universe(RESEARCH_DIR / "universe_B.json.gz")
        errors = frozen_errors + dataset_errors + universe_errors
        signal_errors, signal_evidence = _verify_signal_audit(dataset, signal_summary, candidate_universe)
        errors.extend(signal_errors)
        cases: dict[str, dict[str, Any]] = {}
        case_evidence: dict[str, Any] = {}
        for name in EXPECTED_CASES:
            case_path = RESEARCH_DIR / f"case_{name.replace('/', '_')}.json.gz"
            case = _read_json_gz(case_path)
            cases[name] = case
            case_errors = _verify_case(name, case, config)
            cost_errors, cost_evidence = _verify_costs(name, case, config)
            account_errors, account_evidence = _verify_account(name, case, dataset, config)
            errors.extend(case_errors)
            errors.extend(cost_errors)
            errors.extend(account_errors)
            case_evidence[name] = {
                "errors": case_errors + cost_errors + account_errors,
                "rebalances": len(case.get("rebalances", [])),
                "fills": len(case.get("fills", [])),
                "costs": cost_evidence,
                "account": account_evidence,
            }
        metric_errors, metric_evidence, recomputed_control, recomputed_candidate = _verify_metrics(
            cases, control, candidate
        )
        errors.extend(metric_errors)
        independent_gates = _independent_gates(
            recomputed_control,
            recomputed_candidate,
            coverage,
            signal_summary,
            concentration,
            preflight,
        )
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
        check_count = (
            int(signal_evidence.get("reconstructed_rows", 0))
            + int(metric_evidence.get("metric_fields_checked", 0))
            + sum(int(value.get("rebalances", 0)) + int(value.get("fills", 0)) for value in case_evidence.values())
        )
        verification = {
            "status": "PASS" if not errors else "FAIL",
            "result_validity": result_validity,
            "errors": errors,
            "check_count": check_count,
            "failure_count": len(errors),
            "coverage_description": {
                "signal": "full persisted signal-audit population plus every first/last and session-gap boundary group",
                "fills": "all persisted fills and terminal liquidation events",
                "metrics": "all six cases, all eight non-warmup periods, and every persisted metric field",
            },
            "frozen_input_checks": {"errors": frozen_errors},
            "dataset_checks": {"errors": dataset_errors},
            "universe_checks": {"errors": universe_errors, "evidence": universe_evidence},
            "signal_audit": signal_evidence,
            "case_checks": case_evidence,
            "metric_checks": metric_evidence,
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
        manifest["status"] = (
            "ECONOMIC_RUN_COMPLETE_INDEPENDENTLY_VERIFIED"
            if verification["status"] == "PASS"
            else "ECONOMIC_RUN_COMPLETE_INDEPENDENT_VERIFICATION_FAILED"
        )
        manifest["independent_verification_status"] = verification["status"]
        manifest["result_validity"] = result_validity
        manifest["verdict"] = verdict
        manifest["economic_head_at_verification"] = decision["economic_head_at_verification"]
        _write_json(RESEARCH_DIR / "run_manifest.json", manifest)
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
