"""Independently verify the one-time corrected replay artifacts.

The verifier is deliberately a separate process from the replay runner.  It
re-reads the pinned files and serialized outputs, checks the corrected
structural invariants, and returns a research-validity classification before
the frozen economic gates are interpreted.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot, hash_point_in_time_universe
from parayabuck.core.models import Market
from parayabuck.portfolio.equity_residual_momentum import EquityResidualMomentum1dV2

try:
    from ..candidate import EquityShortTermResidualReversalV1, frozen_reversal_parameters
except ImportError:  # pragma: no cover - direct script execution
    from research.residual_reversal_local_v1.candidate import (
        EquityShortTermResidualReversalV1,
        frozen_reversal_parameters,
    )

ROOT = Path(__file__).resolve().parents[3]
RESEARCH_DIR = ROOT / "research" / "residual_reversal_local_v1"
EXPECTED_BRANCH = "research/residual-reversal-local-v1"
EXPECTED_MAIN_SHA = "8123694a71d8fbb0e520f13b4e1692e932754e97"
EXPECTED_FORENSIC_PARENT = "633c11448387f291d20a8a1aa0ed1286e2e94b34"
EXPECTED_PRICE_SHA = "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"
EXPECTED_ACTION_SHA = "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23"
EXPECTED_LIFETIME_SHA = "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596"
EXPECTED_PROTOCOL_SHA = "bf8a47c20d35835920fb15ac2e75eaf0ea5ebaec3a4f79a0c62e9aa515bf442a"
EXPECTED_V3_SOURCE_SHA = "408de47f8a0e0ac20d926ed3e7d96f01b5747c5db8863ad5e35c146a3cc6b1bc"
EXPECTED_CANDIDATE_PARAMETERS = {
    "formation_bars": 21,
    "skip_bars": 0,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 6,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
    "rebalance_frequency": "weekly",
}
EXPECTED_V3_PARAMETERS = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
    "rebalance_frequency": "weekly",
}
EXPECTED_RISK_LIMITS = {
    "maximum_positions": 6,
    "maximum_single_weight": 0.10,
    "max_position_pct": 0.10,
    "cash_buffer_pct": 0.40,
    "max_gross_exposure_pct": 0.60,
    "daily_loss_limit_pct": 0.005,
    "max_drawdown_pct": 0.05,
}
EXPECTED_EXECUTION_POLICY = {
    "slippage_bps": 2,
    "spread_bps": 2,
    "commission": 0,
    "rebalance_band_pct": 0.005,
    "minimum_order_notional": 10,
}
EXPECTED_STRATEGIES = ("v3", "reversal", "treatment")
EXPECTED_COSTS = (1, 2, 3)
MIN_DECISION_COVERAGE = 90.0
MAX_UNEXPLAINED_GAP_WEEKS = 8
MAX_FINAL_DECISION_LAG_WEEKS = 8
TOLERANCE = 1e-7

if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(raw: object) -> datetime:
    parsed = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _close(left: object, right: object, tolerance: float = TOLERANCE) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance * max(1.0, abs(float(left)), abs(float(right)))
    except (TypeError, ValueError):
        return False


def _resolve(raw: object, *, base: Path = ROOT) -> Path:
    path = Path(str(raw))
    return path if path.is_absolute() else base / path


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class Verification:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, condition: bool, details: Any = None) -> None:
        row: dict[str, Any] = {"name": name, "status": "PASS" if condition else "FAIL"}
        if details is not None:
            row["details"] = details
        self.checks.append(row)

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [row for row in self.checks if row["status"] == "FAIL"]


def _verify_identity(verifier: Verification, output: Path, manifest: dict[str, Any]) -> None:
    verifier.check("corrected output directory", output.is_dir(), str(output))
    verifier.check("branch identity", manifest.get("branch") == EXPECTED_BRANCH, manifest.get("branch"))
    verifier.check("main identity in manifest", manifest.get("main_sha") == EXPECTED_MAIN_SHA, manifest.get("main_sha"))
    verifier.check(
        "forensic parent identity",
        manifest.get("forensic_parent_commit") == EXPECTED_FORENSIC_PARENT,
        manifest.get("forensic_parent_commit"),
    )
    try:
        current_main = subprocess.check_output(["git", "rev-parse", "main"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        current_main = f"error: {exc}"
    verifier.check("current main unchanged", current_main == EXPECTED_MAIN_SHA, current_main)
    for key, expected in (
        ("price_data_sha256", EXPECTED_PRICE_SHA),
        ("corporate_actions_sha256", EXPECTED_ACTION_SHA),
        ("lifetime_table_sha256", EXPECTED_LIFETIME_SHA),
        ("protocol_sha256", EXPECTED_PROTOCOL_SHA),
    ):
        verifier.check(f"manifest {key}", manifest.get(key) == expected, manifest.get(key))

    path_checks = (
        ("price panel", manifest.get("price_data_path"), EXPECTED_PRICE_SHA),
        ("corporate actions", manifest.get("corporate_actions_path"), EXPECTED_ACTION_SHA),
        ("entity lifetimes", manifest.get("lifetime_table_path"), EXPECTED_LIFETIME_SHA),
        ("V3 source", manifest.get("v3_source_path"), EXPECTED_V3_SOURCE_SHA),
    )
    for label, raw_path, expected in path_checks:
        path = _resolve(raw_path)
        exists = path.is_file()
        verifier.check(f"{label} exists", exists, str(path))
        verifier.check(f"{label} SHA-256", exists and _hash_file(path) == expected)

    protocol_path = RESEARCH_DIR / "FROZEN_PROTOCOL.md"
    spec_path = output / "CORRECTED_REPLAY_SPEC.md"
    verifier.check("frozen protocol file identity", protocol_path.is_file() and _hash_file(protocol_path) == EXPECTED_PROTOCOL_SHA)
    verifier.check(
        "corrected replay spec hash pin",
        spec_path.is_file() and _hash_file(spec_path) == manifest.get("corrected_replay_spec_sha256"),
    )
    required_spec_phrases = (
        "validation contains at least one genuine",
        "terminal bar",
        "separate V3/reversal rankings",
        "no cross-sleeve reallocation",
        "CORRECTED_REPLAY_VALID",
    )
    spec_text = spec_path.read_text(encoding="utf-8") if spec_path.is_file() else ""
    verifier.check("corrected spec contains frozen structural contract", all(phrase.lower() in spec_text.lower() for phrase in required_spec_phrases))

    original_hashes = manifest.get("original_artifact_sha256", {})
    verifier.check("original artifact hash manifest complete", set(original_hashes) >= {"FINAL_REPORT.md", "FROZEN_PROTOCOL.md", "FORENSIC_AUDIT.md", "FORENSIC_AUDIT.json"})
    for name, expected in sorted(original_hashes.items()):
        path = RESEARCH_DIR / name
        verifier.check(f"original artifact preserved: {name}", path.is_file() and _hash_file(path) == expected)

    corrected_required = (
        "observation_sets.json",
        "decision_coverage.json",
        "lifecycle_audit.json",
        "sleeve_attribution.json",
        "execution_audit.json",
        "v3_control.json",
        "standalone_reversal.json",
        "treatment.json",
    )
    for name in corrected_required:
        verifier.check(f"corrected artifact exists: {name}", (output / name).is_file())


def _verify_frozen_economics(verifier: Verification, manifest: dict[str, Any]) -> None:
    verifier.check("frozen candidate parameters", manifest.get("candidate_parameters") == EXPECTED_CANDIDATE_PARAMETERS)
    verifier.check("frozen V3 parameters", manifest.get("v3_parameters") == EXPECTED_V3_PARAMETERS)
    verifier.check("frozen maximum allocation", _close(manifest.get("maximum_allocation"), 0.55))
    verifier.check("frozen risk limits", manifest.get("risk_limits") == EXPECTED_RISK_LIMITS, manifest.get("risk_limits"))
    verifier.check("frozen execution policy", manifest.get("execution_policy") == EXPECTED_EXECUTION_POLICY, manifest.get("execution_policy"))
    verifier.check(
        "frozen treatment allocation",
        manifest.get("treatment_allocation") == {"v3": 0.4125, "reversal": 0.1375, "total": 0.55, "cash_remainder": 0.45},
    )
    verifier.check("cost stress set", tuple(manifest.get("cost_multipliers", ())) == EXPECTED_COSTS)
    verifier.check("mechanics frozen before outcomes", manifest.get("mechanics_frozen_before_outcomes") is True)

    candidate = EquityShortTermResidualReversalV1(frozen_reversal_parameters())
    verifier.check("candidate runtime parameters", candidate.parameters == frozen_reversal_parameters())
    inside = [-0.01] * 21
    expected_score = (0.99**21) - 1.0
    score_a = candidate._candidate_score([9.0, *inside], formation=21, skip=0)
    score_b = candidate._candidate_score([-9.0, *inside], formation=21, skip=0)
    verifier.check("exact completed 21-session formation and zero skip", _close(score_a, expected_score) and _close(score_a, score_b))
    verifier.check("negative score has no positive gate", candidate._candidate_passes_gate([], -0.25, formation=21, skip=0))

    original_score = EquityResidualMomentum1dV2._score_candidates

    def stub(_self: object, _context: object) -> tuple[tuple[tuple[float, str], ...], dict[str, float]]:
        return ((0.10, "POSITIVE"), (-0.20, "NEGATIVE")), {"POSITIVE": 1.0, "NEGATIVE": 1.0}

    EquityResidualMomentum1dV2._score_candidates = stub  # type: ignore[method-assign]
    try:
        ranked, _residual_vols = candidate._score_candidates(None)  # type: ignore[arg-type]
    finally:
        EquityResidualMomentum1dV2._score_candidates = original_score  # type: ignore[method-assign]
    verifier.check("negative residual scores rank first", ranked[0] == (-0.20, "NEGATIVE"), ranked)
    verifier.check("candidate minimum history matches beta plus one", candidate.minimum_history_bars == 274)


def _snapshot_from_dict(raw: dict[str, Any]) -> UniverseSnapshot:
    return UniverseSnapshot(
        effective_at=_timestamp(raw["effective_at"]),
        symbols=frozenset(raw["symbols"]),
        scanner_version=str(raw.get("scanner_version", "")),
        config_digest=str(raw.get("config_digest", "")),
        candidate_count=int(raw.get("candidate_count", 0)),
        sector_metadata_coverage=float(raw.get("sector_metadata_coverage", 1.0)),
        observation_set=tuple(raw.get("observation_set", ())),
        maximum_stream_symbols=int(raw.get("maximum_stream_symbols", 0)),
        pinned_symbols=tuple(raw.get("pinned_symbols", ())),
        observation_strategy_name=str(raw.get("observation_strategy_name", "")),
        observation_strategy_version=str(raw.get("observation_strategy_version", "")),
    )


def _verify_observation_sets(verifier: Verification, output: Path) -> dict[str, Any]:
    observation = _load_json(output / "observation_sets.json")
    expected_names = {
        "v3": ["equity_residual_momentum_v3"],
        "reversal": ["equity_short_term_residual_reversal_v1"],
        "treatment": ["treatment_combined_observation_v1"],
    }
    verifier.check("separate observation strategy identities", observation.get("strategy_names") == expected_names, observation.get("strategy_names"))
    verifier.check("shared scanner pool declared", observation.get("scanner_pool_same_across_strategies") is True)
    verifier.check("treatment observation policy identity", observation.get("treatment_policy_name") == "treatment_combined_observation_v1")
    verifier.check("treatment observation policy version", observation.get("treatment_policy_version") == "1.0.0-corrected-replay")
    universes = observation.get("universes", {})
    verifier.check("three corrected observation universes", set(universes) == set(EXPECTED_STRATEGIES))
    reconstructed: dict[str, PointInTimeUniverse] = {}
    for name, compact in universes.items():
        try:
            snapshots = tuple(_snapshot_from_dict(row) for row in compact["snapshots"])
            universe = PointInTimeUniverse(Market.EQUITY, snapshots, str(compact["methodology"]))
            computed = hash_point_in_time_universe(universe)
            reconstructed[name] = universe
            verifier.check(f"{name} PIT universe hash", computed == compact.get("hash") == observation.get("universe_hashes", {}).get(name))
            verifier.check(f"{name} PIT snapshot count", len(snapshots) == int(compact.get("snapshot_count", -1)))
            verifier.check(
                f"{name} observation members are scanner members",
                all(set(snapshot.observation_set) <= set(snapshot.symbols) for snapshot in snapshots),
            )
            verifier.check(
                f"{name} stream capacity respected",
                all(len(snapshot.observation_set) <= snapshot.maximum_stream_symbols for snapshot in snapshots),
            )
        except (KeyError, TypeError, ValueError) as exc:
            verifier.check(f"{name} PIT universe reconstructible", False, str(exc))

    baseline = reconstructed.get("v3")
    if baseline is not None:
        for name in ("reversal", "treatment"):
            candidate = reconstructed.get(name)
            verifier.check(
                f"{name} scanner pool equals V3 pool",
                candidate is not None
                and [(item.effective_at, item.symbols) for item in candidate.snapshots]
                == [(item.effective_at, item.symbols) for item in baseline.snapshots],
            )
    top6 = observation.get("treatment_reversal_top6_coverage", {})
    verifier.check("treatment has reversal top-six access", top6.get("snapshots_with_missing_reversal_top6") == 0)
    verifier.check("treatment top-six coverage spans all snapshots", top6.get("snapshot_count") == len(baseline.snapshots) if baseline else False)
    expected_versions = {
        "v3": {"3.0.0"},
        "reversal": {"1.0.0-research"},
        "treatment": {"1.0.0-corrected-replay"},
    }
    for name, universe in reconstructed.items():
        versions = {snapshot.observation_strategy_version for snapshot in universe.snapshots}
        verifier.check(f"{name} observation version identity", versions == expected_versions[name], versions)
    return observation


def _load_lifetime_map(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("current_status", "")).lower() != "inactive":
                continue
            symbol = str(row["canonical_symbol"]).strip().upper()
            result[symbol] = {
                "last_bar": _timestamp(row["last_observed_bar"]),
                "terminal_event_class": row.get("terminal_event_class") or "UNKNOWN",
            }
    return result


def _verify_lifecycle(verifier: Verification, output: Path, manifest: dict[str, Any], execution: dict[str, Any]) -> None:
    lifecycle = _load_json(output / "lifecycle_audit.json")
    lifetime_path = _resolve(manifest["lifetime_table_path"])
    terminal_map = _load_lifetime_map(lifetime_path)
    runs = lifecycle.get("runs", {})
    expected_runs = {f"{name}_{cost}x" for name in EXPECTED_STRATEGIES for cost in EXPECTED_COSTS}
    verifier.check("lifecycle run coverage", set(runs) == expected_runs)
    terminal_event_keys: set[tuple[str, datetime]] = set()
    total_terminal_events = 0
    for key in sorted(expected_runs):
        row = runs.get(key, {})
        audit = execution.get(key, {})
        verifier.check(f"{key} terminal map loaded", row.get("terminal_map_loaded") is True and audit.get("terminal_map_loaded") is True)
        verifier.check(f"{key} no inactive holdings remain", row.get("inactive_holdings_remaining") == [])
        events = [event for event in audit.get("terminal_events", []) if event.get("policy") == "TERMINAL_BAR"]
        total_terminal_events += len(events)
        verifier.check(f"{key} lifecycle event serialization", events == row.get("terminal_bar_events", []))
        closed = {(_timestamp(item[0]), str(item[1]).upper()) for item in audit.get("closed_position_events", [])}
        for event in events:
            symbol = str(event.get("symbol", "")).upper()
            timestamp = _timestamp(event.get("timestamp"))
            terminal_event_keys.add((symbol, timestamp))
            expected = terminal_map.get(symbol)
            verifier.check(f"{key} terminal symbol is inactive", expected is not None, symbol)
            if expected is None:
                continue
            verifier.check(f"{key} terminal timestamp is lifetime final bar", timestamp == expected["last_bar"], {"event": timestamp.isoformat(), "map": expected["last_bar"].isoformat()})
            verifier.check(f"{key} terminal event class preserved", event.get("terminal_event_class") == expected["terminal_event_class"])
            verifier.check(f"{key} terminal event uses real positive close", _finite(event.get("last_close")) and float(event["last_close"]) > 0)
            verifier.check(f"{key} terminal event closes a held position", (timestamp, symbol) in closed)
            verifier.check(f"{key} terminal event has no fake fill semantics", event.get("effective_policy") == "TERMINAL_BAR" and float(event.get("quantity", 0)) > 0)
    verifier.check("corrected replay exercised terminal-bar lifecycle", total_terminal_events > 0)

    wanted = terminal_event_keys
    found: dict[tuple[str, datetime], float] = {}
    if wanted:
        price_path = _resolve(manifest["price_data_path"])
        with price_path.open(newline="", encoding="utf-8") as handle:
            for raw in csv.DictReader(handle):
                symbol = str(raw.get("symbol", "")).strip().upper()
                if symbol not in {item[0] for item in wanted}:
                    continue
                timestamp = _timestamp(raw["timestamp"])
                key = (symbol, timestamp)
                if key in wanted:
                    found[key] = float(raw["close"])
        verifier.check("terminal prices found in pinned panel", found.keys() == wanted, {"wanted": len(wanted), "found": len(found)})
        for key, actual_close in found.items():
            for run_key, audit in execution.items():
                for event in audit.get("terminal_events", []):
                    if event.get("policy") == "TERMINAL_BAR" and (str(event.get("symbol")).upper(), _timestamp(event.get("timestamp"))) == key:
                        verifier.check(f"{run_key} terminal close matches pinned panel", _close(event.get("last_close"), actual_close))


def _period_rows(run: dict[str, Any], period: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = run.get(period, {})
    attempts = list(data.get("attempts", []))
    return attempts, data


def _verify_coverage(verifier: Verification, output: Path) -> dict[str, Any]:
    coverage = _load_json(output / "decision_coverage.json")
    thresholds = coverage.get("thresholds", {})
    verifier.check("coverage threshold identity", thresholds == {
        "minimum_decision_coverage_pct": 90.0,
        "maximum_unexplained_gap_weeks": 8,
        "maximum_final_decision_lag_weeks": 8,
        "validation_requires_decision": True,
    }, thresholds)
    runs = coverage.get("runs", {})
    expected_runs = {f"{name}_{cost}x" for name in EXPECTED_STRATEGIES for cost in EXPECTED_COSTS}
    verifier.check("coverage run coverage", set(runs) == expected_runs)
    baseline_schedule: dict[str, list[str]] = {}
    for key in sorted(expected_runs):
        run = runs.get(key, {})
        attempts_all = list(run.get("attempts", []))
        timestamps = [_timestamp(row["timestamp"]) for row in attempts_all]
        verifier.check(f"{key} attempts are ordered", timestamps == sorted(timestamps))
        weeks = [(timestamp.isocalendar().year, timestamp.isocalendar().week) for timestamp in timestamps]
        verifier.check(f"{key} one scheduled attempt per ISO week", len(weeks) == len(set(weeks)))
        verifier.check(
            f"{key} decision attempts are distinct from fills",
            all(row.get("scheduled") is True and "fills" not in row and isinstance(row.get("decision_recorded"), bool) for row in attempts_all),
        )
        for period in ("development", "validation"):
            attempts, data = _period_rows(run, period)
            decisions = sum(bool(row.get("decision_recorded")) for row in attempts)
            computed_coverage = 100.0 * decisions / len(attempts) if attempts else 0.0
            period_timestamps = [_timestamp(row["timestamp"]) for row in attempts]
            verifier.check(f"{key} {period} scheduled count identity", data.get("scheduled_opportunities") == len(attempts))
            verifier.check(f"{key} {period} decision count identity", data.get("decisions") == decisions)
            verifier.check(f"{key} {period} coverage arithmetic", _close(data.get("coverage_pct"), computed_coverage, 1e-9))
            verifier.check(f"{key} {period} persisted schedule identity", data.get("eligible_schedule_timestamps") == [row["timestamp"] for row in attempts])
            verifier.check(f"{key} {period} decisions are genuine", decisions > 0)
            verifier.check(f"{key} {period} coverage meets frozen threshold", float(data.get("coverage_pct", 0)) >= MIN_DECISION_COVERAGE)
            verifier.check(f"{key} {period} unexplained gap meets threshold", int(data.get("longest_unexplained_gap_weeks", 999999)) <= MAX_UNEXPLAINED_GAP_WEEKS)
            verifier.check(f"{key} {period} final decision lag meets threshold", int(data.get("decision_recency_lag_weeks", 999999)) <= MAX_FINAL_DECISION_LAG_WEEKS)
            verifier.check(f"{key} {period} attempts lie in period", all(_timestamp(run["attempts"][0]["timestamp"]) <= item <= _timestamp(run["attempts"][-1]["timestamp"]) for item in period_timestamps) if period_timestamps and attempts_all else True)
            verifier.check(f"{key} {period} fills are separately counted", isinstance(data.get("fills"), int) and data.get("fills", -1) >= 0)
            if period == "validation":
                verifier.check(f"{key} validation begins after frozen boundary", all(item >= datetime(2022, 1, 3, tzinfo=UTC) for item in period_timestamps))
            if period == "development":
                verifier.check(f"{key} development ends before frozen boundary", all(item < datetime(2022, 1, 3, tzinfo=UTC) for item in period_timestamps))
        baseline_schedule.setdefault(key.split("_")[0], [row["timestamp"] for row in runs[key]["validation"]["attempts"]])
    for name in EXPECTED_STRATEGIES:
        schedules = [
            [row["timestamp"] for row in runs[f"{name}_{cost}x"]["validation"]["attempts"]]
            for cost in EXPECTED_COSTS
        ]
        verifier.check(f"{name} validation schedule stable across cost stress", all(schedule == schedules[0] for schedule in schedules[1:]))
    return coverage


def _expected_fill_costs(fill: dict[str, Any], cost: int, execution_policy: dict[str, Any]) -> tuple[float, float, float]:
    slippage_bps = float(execution_policy["slippage_bps"]) * cost
    spread_bps = float(execution_policy["spread_bps"]) * cost
    reference_notional = float(fill["qty"]) * float(fill["reference_price"])
    slippage = reference_notional * slippage_bps / 10_000.0
    spread = reference_notional * spread_bps / 2.0 / 10_000.0
    adverse = (slippage_bps + spread_bps / 2.0) / 10_000.0
    expected_fill = float(fill["reference_price"]) * (1.0 + adverse if fill["side"] == "buy" else 1.0 - adverse)
    return slippage, spread, expected_fill


def _verify_execution(verifier: Verification, output: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    execution = _load_json(output / "execution_audit.json")
    payloads = {
        "v3": _load_json(output / "v3_control.json"),
        "reversal": _load_json(output / "standalone_reversal.json"),
        "treatment": _load_json(output / "treatment.json"),
    }
    expected_runs = {f"{name}_{cost}x" for name in EXPECTED_STRATEGIES for cost in EXPECTED_COSTS}
    verifier.check("execution run coverage", set(execution) == expected_runs)
    for name, payload in payloads.items():
        verifier.check(f"{name} payload strategy identity", payload.get("strategy") in {"equity_residual_momentum_v3", "equity_short_term_residual_reversal_v1", "equity_v3_plus_residual_reversal_treatment"})
        for cost in EXPECTED_COSTS:
            key = f"{name}_{cost}x"
            audit = execution.get(key, {})
            full = payload.get("costs", {}).get(str(cost), {}).get("full", {})
            curve = list(audit.get("curve", []))
            timestamps = [_timestamp(item[0]) for item in curve]
            values = [float(item[1]) for item in curve]
            label = f"{name} {cost}x"
            verifier.check(f"{label} curve ordered and finite", bool(curve) and timestamps == sorted(timestamps) and len(timestamps) == len(set(timestamps)) and all(math.isfinite(value) and value > 0 for value in values))
            if not curve:
                continue
            verifier.check(f"{label} ending NAV identity", _close(audit.get("ending_equity"), values[-1]) and _close(full.get("ending_equity"), values[-1]))
            expected_return = values[-1] / float(audit.get("starting_equity", 0)) - 1.0
            verifier.check(f"{label} return identity", _close(audit.get("total_return"), expected_return) and _close(full.get("total_return"), expected_return))
            fills = list(audit.get("fills", []))
            decisions = {_timestamp(row["decision_timestamp"]) for row in audit.get("rebalances", [])}
            fill_cost_ok = True
            fill_order_ok = True
            for fill in fills:
                decision_timestamp = _timestamp(fill["decision_timestamp"])
                fill_timestamp = _timestamp(fill["timestamp"])
                fill_order_ok = fill_order_ok and decision_timestamp < fill_timestamp and decision_timestamp in decisions
                expected_slippage, expected_spread, expected_price = _expected_fill_costs(fill, cost, manifest["execution_policy"])
                fill_cost_ok = fill_cost_ok and _close(fill.get("slippage_cost"), expected_slippage, 1e-9)
                fill_cost_ok = fill_cost_ok and _close(fill.get("spread_cost"), expected_spread, 1e-9)
                fill_cost_ok = fill_cost_ok and _close(fill.get("fill_price"), expected_price, 1e-9)
                fill_cost_ok = fill_cost_ok and all(_finite(fill.get(field)) and float(fill[field]) >= 0 for field in ("qty", "reference_price", "fill_price", "slippage_cost", "spread_cost", "fee"))
            verifier.check(f"{label} next-bar fills tied to decisions", fill_order_ok)
            verifier.check(f"{label} fill cost arithmetic", fill_cost_ok)

            terminal = [event for event in audit.get("terminal_events", []) if event.get("policy") in {"TERMINAL_BAR", "FINAL_BAR"}]
            expected_slippage_total = sum(_expected_fill_costs(fill, cost, manifest["execution_policy"])[0] for fill in fills)
            expected_spread_total = sum(_expected_fill_costs(fill, cost, manifest["execution_policy"])[1] for fill in fills)
            expected_fee_total = sum(float(fill["fee"]) for fill in fills)
            for event in terminal:
                quantity = float(event.get("quantity", 0))
                last_close = float(event.get("last_close", 0))
                expected_slippage_total += quantity * last_close * float(manifest["execution_policy"]["slippage_bps"]) * cost / 10_000.0
                expected_spread_total += quantity * last_close * float(manifest["execution_policy"]["spread_bps"]) * cost / 2.0 / 10_000.0
                expected_fee_total += float(event.get("fee", 0.0))
            verifier.check(f"{label} aggregate cost arithmetic", _close(audit.get("slippage_cost_total"), expected_slippage_total, 1e-8) and _close(audit.get("spread_cost_total"), expected_spread_total, 1e-8) and _close(audit.get("fee_total"), expected_fee_total, 1e-8))

            rebalances = list(audit.get("rebalances", []))
            rebalance_timestamps = [_timestamp(row["decision_timestamp"]) for row in rebalances]
            weeks = [(timestamp.isocalendar().year, timestamp.isocalendar().week) for timestamp in rebalance_timestamps]
            verifier.check(f"{label} weekly decision uniqueness", len(weeks) == len(set(weeks)))
            target_ok = True
            for rebalance in rebalances:
                weights = [float(pair[1]) for pair in rebalance.get("target_weights", [])]
                target_ok = target_ok and len(weights) <= 6 and all(0.0 <= weight <= 0.10 + TOLERANCE for weight in weights)
                target_ok = target_ok and sum(weights) <= 0.55 + TOLERANCE
                target_ok = target_ok and 0.0 <= float(rebalance.get("target_cash_weight", -1)) <= 1.0
                target_ok = target_ok and _close(sum(weights) + float(rebalance.get("target_cash_weight", 0)), 1.0, 1e-6)
            verifier.check(f"{label} target caps and cash identity", target_ok)
            state_cash = audit.get("state_cash")
            holdings = dict(audit.get("state_holdings", {}))
            prices = dict(audit.get("state_last_prices", {}))
            verifier.check(f"{label} final cash finite/non-negative", _finite(state_cash) and float(state_cash) >= -TOLERANCE)
            verifier.check(f"{label} final lifecycle has no holdings", holdings == {})
            nav = float(state_cash) + sum(float(quantity) * float(prices[symbol]) for symbol, quantity in holdings.items() if symbol in prices) if _finite(state_cash) else float("nan")
            verifier.check(f"{label} final state NAV identity", _close(nav, values[-1], 1e-8))
            verifier.check(f"{label} summary counts identity", full.get("fill_count") == len(fills) and full.get("rebalanced_decision_count") == len(rebalances))
            verifier.check(f"{label} concentration finite", _finite(full.get("maximum_concentration")) and float(full.get("maximum_concentration", -1)) >= 0)
    return execution


def _verify_sleeves(verifier: Verification, output: Path, execution: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    sleeve = _load_json(output / "sleeve_attribution.json")
    verifier.check("sleeve policy identity", sleeve.get("policy") == "sleeve_aware_quota_round_robin_v1")
    verifier.check("sleeve intended allocation identity", sleeve.get("frozen_intended_allocation") == {"v3": 0.4125, "reversal": 0.1375, "total": 0.55})
    by_cost = sleeve.get("by_cost", {})
    for cost in EXPECTED_COSTS:
        key = f"{cost}x"
        summary = by_cost.get(key, {})
        attrs = list(summary.get("per_decision", []))
        treatment_audit = execution.get(f"treatment_{key}", {})
        rebalances = list(treatment_audit.get("rebalances", []))
        verifier.check(f"treatment {key} attribution count", summary.get("decision_count") == len(attrs) == len(rebalances) == coverage["runs"][f"treatment_{key}"]["validation"].get("decisions", 0) + coverage["runs"][f"treatment_{key}"]["development"].get("decisions", 0))
        for index, (row, rebalance) in enumerate(zip(attrs, rebalances, strict=False)):
            source = row.get("source_contributions", {})
            selected = set(row.get("post_cap_symbols", []))
            target = {str(symbol): float(weight) for symbol, weight in rebalance.get("target_weights", [])}
            recorded_target = {str(symbol): float(weight) for symbol, weight in row.get("post_overlay_target_weights", [])}
            verifier.check(f"treatment {key} decision {index} source ledger", isinstance(source, dict) and bool(row.get("no_cross_sleeve_reallocation")) and all(set(value) >= {"v3", "reversal"} for value in source.values()))
            verifier.check(f"treatment {key} decision {index} six-position capacity", len(selected) <= 6 and len(target) <= 6 and len(recorded_target) <= 6)
            verifier.check(f"treatment {key} decision {index} per-symbol cap", all(0.0 <= weight <= 0.10 + TOLERANCE for weight in target.values()))
            verifier.check(f"treatment {key} decision {index} no reallocation", float(row.get("post_overlay_v3_exposure", 0)) <= 0.4125 + TOLERANCE and float(row.get("post_overlay_reversal_exposure", 0)) <= 0.1375 + TOLERANCE and float(row.get("post_overlay_unused_v3_allocation", -1)) >= -TOLERANCE and float(row.get("post_overlay_unused_reversal_allocation", -1)) >= -TOLERANCE)
            verifier.check(f"treatment {key} decision {index} target attribution matches execution", target == recorded_target)
            verifier.check(f"treatment {key} decision {index} target exposure identity", _close(sum(target.values()), float(row.get("post_overlay_v3_exposure", 0)) + float(row.get("post_overlay_reversal_exposure", 0)), 1e-6))
            verifier.check(f"treatment {key} decision {index} unused capacity stays cash", _close(float(rebalance.get("target_cash_weight", 0)) + sum(target.values()), 1.0, 1e-6))
            verifier.check(f"treatment {key} decision {index} dropped symbols attributed", set(row.get("dropped_symbols", {})) <= set(row.get("source_ranks", {})) and all(reason for reason in row.get("dropped_symbol_reasons", {}).values()))
    return sleeve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    verifier = Verification()
    try:
        manifest = _load_json(output / "replay_manifest.json")
        _verify_identity(verifier, output, manifest)
        verifier.check("economic results are present for verification", manifest.get("economic_results_generated") is True)
        _verify_frozen_economics(verifier, manifest)
        _verify_observation_sets(verifier, output)
        execution = _verify_execution(verifier, output, manifest)
        coverage = _verify_coverage(verifier, output)
        _verify_lifecycle(verifier, output, manifest, execution)
        _verify_sleeves(verifier, output, execution, coverage)
    except Exception as exc:  # a malformed artifact is an invalid replay, not a verifier crash
        verifier.check("verifier completed without artifact exception", False, f"{type(exc).__name__}: {exc}")

    status = "CORRECTED_REPLAY_VALID" if not verifier.failed else "CORRECTED_REPLAY_INVALID"
    payload = {
        "status": status,
        "verifier_version": "corrected-independent-verifier-v1",
        "checks": verifier.checks,
        "passed_checks": len(verifier.checks) - len(verifier.failed),
        "failure_count": len(verifier.failed),
        "failures": verifier.failed,
        "verifier": "research/residual_reversal_local_v1/corrected_replay_v1/verify_corrected_replay.py",
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "corrected_independent_verification.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for row in verifier.failed:
        print(f"FAIL: {row['name']}: {row.get('details', '')}")
    print(f"Corrected independent verification: {status} ({payload['passed_checks']} passed, {payload['failure_count']} failed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
