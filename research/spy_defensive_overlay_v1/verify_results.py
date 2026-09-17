"""Independent, fail-closed verification of the frozen SPY-overlay replay.

This module intentionally does not import the candidate or replay modules.  It
reconstructs the protocol invariants from the persisted JSON artifacts and the
frozen CSV inputs only.  It does not calculate or judge economic acceptance
gates.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CAMPAIGN = "equity_spy_defensive_overlay_v1"
FROZEN_MAIN_SHA = "5175a58b2fb4084e5c5a00854d3c3c94b0e819a1"
FROZEN_BRANCH = "research/spy-defensive-overlay-v1"
FROZEN_HASHES = {
    "data/historical/equity_pit_symmetric_2016_2026_v5.csv": (
        "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139"
    ),
    "data/historical/equity_entity_lifetimes_full_v5.csv": (
        "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596"
    ),
    "data/historical/equity_survivorship_full_corporate_actions_v5.csv": (
        "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23"
    ),
}
BAR_PATH = "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
LIFETIME_PATH = "data/historical/equity_entity_lifetimes_full_v5.csv"
PROTOCOL_PATH = "research/spy_defensive_overlay_v1/FROZEN_PROTOCOL.md"
V3_PATH = "src/parayabuck/portfolio/equity_residual_momentum_v3.py"
ARMS = ("A_incumbent", "B_defensive_cash", "C_defensive_sh")
COSTS = (1, 2, 3)
EXPECTED_RUN_KEYS = {f"{arm}_{cost}x" for arm in ARMS for cost in COSTS}
FULL_START = datetime(2017, 1, 3, tzinfo=UTC)
DEVELOPMENT_END = datetime(2021, 12, 31, 23, 59, 59, tzinfo=UTC)
VALIDATION_START = datetime(2022, 1, 3, tzinfo=UTC)
FULL_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
TOLERANCE = 1e-8


@dataclass(frozen=True)
class VerificationExpectations:
    """Frozen identities; injectable only to permit tiny synthetic tests."""

    campaign: str = CAMPAIGN
    cleaned_main_sha: str = FROZEN_MAIN_SHA
    branch: str = FROZEN_BRANCH
    data_hashes: Mapping[str, str] = field(default_factory=lambda: dict(FROZEN_HASHES))
    full_start: datetime = FULL_START
    development_end: datetime = DEVELOPMENT_END
    validation_start: datetime = VALIDATION_START
    full_end: datetime = FULL_END
    base_slippage_bps: float = 2.0
    base_spread_bps: float = 2.0


class _Checks:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, name: str, passed: bool, detail: str = "") -> bool:
        row = {"name": name, "status": "PASS" if passed else "FAIL"}
        if detail:
            row["detail"] = detail
        self.rows.append(row)
        return passed

    def equal(self, name: str, actual: Any, expected: Any) -> bool:
        return self.add(name, actual == expected, f"actual={actual!r}; expected={expected!r}")

    def close(self, name: str, actual: float, expected: float, *, tol: float = TOLERANCE) -> bool:
        passed = math.isfinite(actual) and math.isfinite(expected) and abs(actual - expected) <= tol
        return self.add(name, passed, f"actual={actual:.12g}; expected={expected:.12g}; tolerance={tol}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _timestamp(value: Any) -> datetime:
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"non-finite number: {value!r}")
    return result


def _git(repo_root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo_root, text=True).strip()


def _weights(row: Mapping[str, Any]) -> dict[str, float]:
    pairs = row.get("target_weights", [])
    result: dict[str, float] = {}
    for symbol, weight in pairs:
        if symbol in result:
            raise ValueError(f"duplicate target symbol {symbol}")
        result[str(symbol)] = _finite(weight)
    return result


def _diagnostic_map(audit: Mapping[str, Any]) -> dict[datetime, Mapping[str, Any]]:
    return {_timestamp(key): value for key, value in audit.get("decision_diagnostics", {}).items()}


def _rebalance_map(audit: Mapping[str, Any]) -> dict[datetime, Mapping[str, Any]]:
    return {_timestamp(row["decision_timestamp"]): row for row in audit.get("rebalances", [])}


def _load_bars(path: Path, wanted: set[str]) -> dict[str, dict[datetime, dict[str, float]]]:
    result: dict[str, dict[datetime, dict[str, float]]] = {symbol: {} for symbol in wanted}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row.get("symbol", "")).upper()
            if symbol not in result:
                continue
            timestamp = _timestamp(row["timestamp"])
            values = {name: _finite(row[name]) for name in ("open", "high", "low", "close", "volume")}
            if timestamp in result[symbol]:
                raise ValueError(f"duplicate {symbol} bar at {timestamp.isoformat()}")
            result[symbol][timestamp] = values
    return result


def _load_lifetimes(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {str(row["canonical_symbol"]).upper(): dict(row) for row in csv.DictReader(handle)}


def _scheduled_spy(spy: Mapping[datetime, Any], expected: VerificationExpectations) -> list[datetime]:
    timestamps = sorted(timestamp for timestamp in spy if expected.full_start <= timestamp <= expected.full_end)
    scheduled: list[datetime] = []
    previous_week: tuple[int, int] | None = None
    for timestamp in timestamps:
        week = timestamp.isocalendar()[:2]
        if week != previous_week:
            scheduled.append(timestamp)
        previous_week = week
    return scheduled


def _source_weights(diagnostic: Mapping[str, Any]) -> list[tuple[str, float, int]]:
    return [
        (str(row["symbol"]), _finite(row["weight"]), int(row["rank"]))
        for row in diagnostic.get("source_v3_weights", [])
    ]


def _verify_identity(
    checks: _Checks,
    repo_root: Path,
    manifest: Mapping[str, Any],
    results: Mapping[str, Any],
    expected: VerificationExpectations,
    git_reader: Callable[..., str],
) -> None:
    checks.equal("identity.manifest_campaign", manifest.get("campaign"), expected.campaign)
    checks.equal("identity.results_campaign", results.get("campaign"), expected.campaign)
    checks.equal("identity.cleaned_main_sha", manifest.get("cleaned_main_sha"), expected.cleaned_main_sha)
    checks.equal("identity.branch", manifest.get("branch"), expected.branch)
    checks.equal("identity.current_main_sha", git_reader(repo_root, "rev-parse", "main"), expected.cleaned_main_sha)
    checks.equal("identity.current_branch", git_reader(repo_root, "branch", "--show-current"), expected.branch)
    head = str(manifest.get("research_head_at_run", ""))
    checks.add("identity.research_head_at_run", len(head) == 40 and all(char in "0123456789abcdef" for char in head))
    checks.equal("identity.protocol_sha256", manifest.get("protocol_sha256"), _sha256(repo_root / PROTOCOL_PATH))
    checks.equal("identity.v3_source_sha256", manifest.get("v3_source_sha256"), _sha256(repo_root / V3_PATH))
    checks.equal("identity.paper_unchanged", manifest.get("paper_config_unchanged"), True)
    checks.equal("identity.live_disabled", manifest.get("live_unchanged_disabled"), True)

    manifest_hashes = manifest.get("data_hashes", {})
    results_hashes = results.get("data_hashes", {})
    for relative, digest in expected.data_hashes.items():
        checks.equal(f"data.manifest_hash.{Path(relative).name}", manifest_hashes.get(relative), digest)
        checks.equal(f"data.results_hash.{Path(relative).name}", results_hashes.get(relative), digest)
        checks.equal(f"data.actual_hash.{Path(relative).name}", _sha256(repo_root / relative), digest)


def _verify_data(
    checks: _Checks, bars: Mapping[str, Mapping[datetime, Mapping[str, float]]], lifetimes: Mapping[str, Any]
) -> None:
    for symbol in ("SPY", "SH"):
        rows = bars.get(symbol, {})
        checks.add(f"data.{symbol}.present", bool(rows))
        checks.add(
            f"data.{symbol}.real_positive_ohlcv",
            bool(rows)
            and all(
                row["open"] > 0 and row["high"] > 0 and row["low"] > 0 and row["close"] > 0 and row["volume"] > 0
                for row in rows.values()
            ),
            "raw CSV rows only; zero/negative placeholders are forbidden",
        )
        lifetime = lifetimes.get(symbol, {})
        checks.equal(
            f"data.{symbol}.lifetime_active",
            str(lifetime.get("current_status", "")).upper(),
            "ACTIVE",
        )
        checks.equal(f"data.{symbol}.pit_tier", lifetime.get("pit_quality_tier"), "TIER_A_VERIFIED_PIT")


def _verify_signal_and_semantics(
    checks: _Checks,
    audits: Mapping[str, Mapping[str, Any]],
    results: Mapping[str, Any],
    spy: Mapping[datetime, Mapping[str, float]],
    sh: Mapping[datetime, Mapping[str, float]],
) -> None:
    spy_timestamps = sorted(spy)
    spy_closes = [spy[timestamp]["close"] for timestamp in spy_timestamps]

    for cost in COSTS:
        per_arm = {arm: audits[f"{arm}_{cost}x"] for arm in ARMS}
        maps = {arm: _rebalance_map(audit) for arm, audit in per_arm.items()}
        diagnostics = {arm: _diagnostic_map(audit) for arm, audit in per_arm.items()}
        checks.add(
            f"semantics.{cost}x.equal_decision_timestamps",
            all(set(maps[arm]) == set(maps[ARMS[0]]) for arm in ARMS[1:]),
        )
        for timestamp in sorted(maps[ARMS[0]]):
            label = f"{cost}x.{timestamp.isoformat()}"
            index = bisect.bisect_right(spy_timestamps, timestamp) - 1
            enough = index >= 252
            current = enough and spy_timestamps[index] == timestamp
            checks.add(f"signal.{label}.253_completed", enough and current)
            if not enough or not current:
                continue
            trend = spy_closes[index] / spy_closes[index - 252] - 1.0
            regime = "NORMAL" if trend > 0 else "DEFENSIVE"
            for arm in ARMS:
                diagnostic = diagnostics[arm].get(timestamp)
                checks.add(f"signal.{label}.{arm}.diagnostic_present", diagnostic is not None)
                if diagnostic is None:
                    continue
                checks.close(f"signal.{label}.{arm}.trend", _finite(diagnostic["trend_return"]), trend)
                checks.equal(f"signal.{label}.{arm}.regime", diagnostic.get("regime"), regime)
                checks.equal(f"signal.{label}.{arm}.observations", diagnostic.get("signal_observations"), 253)
                checks.equal(f"semantics.{label}.{arm}.diagnostic_arm", diagnostic.get("arm"), arm)
                checks.equal(
                    f"semantics.{label}.{arm}.no_reallocation", diagnostic.get("no_unused_allocation_reallocated"), True
                )
            for arm in ARMS:
                diagnostic = diagnostics[arm][timestamp]
                source = _source_weights(diagnostic)
                source_symbols = {symbol for symbol, _weight, _rank in source}
                checks.add(f"semantics.{label}.{arm}.v3_source_count", len(source) <= 8)
                checks.add(
                    f"semantics.{label}.{arm}.v3_source_weights",
                    all(0.0 < weight <= 0.10 + TOLERANCE for _symbol, weight, _rank in source),
                )
                checks.equal(
                    f"semantics.{label}.{arm}.v3_source_ranks",
                    sorted(rank for _symbol, _weight, rank in source),
                    list(range(1, len(source) + 1)),
                )
                final_symbols = set(_weights(maps[arm][timestamp]))
                allowed_symbols = source_symbols | (
                    {"SH"} if arm == "C_defensive_sh" and regime == "DEFENSIVE" else set()
                )
                checks.add(f"semantics.{label}.{arm}.post_risk_symbol_subset", final_symbols <= allowed_symbols)

            if regime == "NORMAL":
                for arm in ARMS:
                    diagnostic = diagnostics[arm][timestamp]
                    checks.close(f"semantics.{label}.{arm}.normal_scale", _finite(diagnostic["v3_scale"]), 1.0)
                    checks.close(
                        f"semantics.{label}.{arm}.normal_overlay",
                        _finite(diagnostic["intended_overlay_sh_exposure"]),
                        0.0,
                    )
                    source_total = sum(weight for _symbol, weight, _rank in _source_weights(diagnostic))
                    checks.close(
                        f"semantics.{label}.{arm}.normal_pre_overlay_total",
                        _finite(diagnostic["pre_overlay_v3_exposure"]),
                        source_total,
                    )
            else:
                expected_scale = 0.45 / 0.55
                checks.close(f"semantics.{label}.A.scale", _finite(diagnostics[ARMS[0]][timestamp]["v3_scale"]), 1.0)
                checks.close(
                    f"semantics.{label}.A.intended_v3",
                    _finite(diagnostics[ARMS[0]][timestamp]["intended_v3_exposure"]),
                    0.55,
                )
                for arm in ARMS[1:]:
                    diagnostic = diagnostics[arm][timestamp]
                    checks.close(
                        f"semantics.{label}.{arm}.fixed_scale", _finite(diagnostic["v3_scale"]), expected_scale
                    )
                    checks.close(
                        f"semantics.{label}.{arm}.intended_v3", _finite(diagnostic["intended_v3_exposure"]), 0.45
                    )
                    expected_pre = (
                        sum(weight for _symbol, weight, _rank in _source_weights(diagnostic)) * expected_scale
                    )
                    checks.close(
                        f"semantics.{label}.{arm}.no_scale_up",
                        _finite(diagnostic["pre_overlay_v3_exposure"]),
                        expected_pre,
                    )
                checks.close(
                    f"semantics.{label}.B.no_overlay",
                    _finite(diagnostics[ARMS[1]][timestamp]["intended_overlay_sh_exposure"]),
                    0.0,
                )
                c = diagnostics[ARMS[2]][timestamp]
                checks.close(f"semantics.{label}.C.overlay_intended", _finite(c["intended_overlay_sh_exposure"]), 0.10)
                checks.equal(f"semantics.{label}.C.sh_slot_reserved", c.get("sh_slot_reserved"), True)
                checks.equal(
                    f"semantics.{label}.C.maximum_non_sh", c.get("maximum_non_sh_v3_positions_after_overlay"), 5
                )
                checks.add(f"data.{label}.C.current_real_sh", timestamp in sh and sh[timestamp]["close"] > 0)
                v3_owned = _finite(c["v3_owned_sh"])
                overlay_owned = _finite(c["realized_overlay_sh_exposure"])
                checks.close(f"semantics.{label}.C.sh_merge", _finite(c["total_sh_exposure"]), v3_owned + overlay_owned)
                checks.add(f"semantics.{label}.C.sh_pre_overlay_cap", v3_owned + overlay_owned <= 0.10 + TOLERANCE)

    result_runs = results.get("runs", {})
    for key, audit in audits.items():
        run_result = result_runs.get(key, {})
        capacity_by_time = {_timestamp(row["timestamp"]): row for row in run_result.get("capacity", {}).get("rows", [])}
        audit_diagnostics = _diagnostic_map(audit)
        for timestamp, diagnostic in audit_diagnostics.items():
            if diagnostic.get("regime") == "DEFENSIVE":
                checks.add(f"capacity.{key}.{timestamp.isoformat()}.row_present", timestamp in capacity_by_time)


def _verify_allocations(checks: _Checks, audits: Mapping[str, Mapping[str, Any]]) -> None:
    for key, audit in audits.items():
        for row in audit.get("rebalances", []):
            timestamp = _timestamp(row["decision_timestamp"])
            weights = _weights(row)
            label = f"allocation.{key}.{timestamp.isoformat()}"
            total = sum(weights.values())
            cash = _finite(row["target_cash_weight"])
            checks.add(f"{label}.nonnegative", all(weight >= -TOLERANCE for weight in weights.values()))
            checks.add(f"{label}.per_asset_cap", all(weight <= 0.10 + TOLERANCE for weight in weights.values()))
            checks.add(f"{label}.six_positions", len(weights) <= 6)
            checks.add(f"{label}.equity_sleeve", total <= 0.55 + TOLERANCE)
            checks.add(f"{label}.gross", total <= 0.60 + TOLERANCE)
            checks.add(f"{label}.cash_floor", cash >= 0.40 - TOLERANCE)
            checks.close(f"{label}.cash_reconciliation", total + cash, 1.0)
            arm = str(audit.get("arm"))
            diagnostic = _diagnostic_map(audit).get(timestamp, {})
            if arm == "C_defensive_sh" and diagnostic.get("regime") == "DEFENSIVE":
                checks.add(f"{label}.treatment_total_slots", len(weights) <= 6)
                checks.add(f"{label}.treatment_v3_slots", sum(symbol != "SH" for symbol in weights) <= 5)
                checks.add(f"{label}.treatment_sh_cap", weights.get("SH", 0.0) <= 0.10 + TOLERANCE)


def _verify_coverage(
    checks: _Checks,
    audits: Mapping[str, Mapping[str, Any]],
    results: Mapping[str, Any],
    spy: Mapping[datetime, Any],
    expected: VerificationExpectations,
) -> None:
    scheduled = _scheduled_spy(spy, expected)
    result_runs = results.get("runs", {})
    for key, audit in audits.items():
        coverage = result_runs.get(key, {}).get("coverage", {})
        rebalances = sorted(_rebalance_map(audit))
        skips = coverage.get("skips", [])
        checks.equal(f"coverage.{key}.scheduled", coverage.get("scheduled_opportunities"), len(scheduled))
        checks.equal(f"coverage.{key}.recorded", coverage.get("recorded_decisions"), len(rebalances))
        checks.equal(f"coverage.{key}.skip_count", coverage.get("skipped_decisions"), len(skips))
        checks.equal(f"coverage.{key}.accounting", len(scheduled), len(rebalances) + len(skips))
        checks.equal(f"coverage.{key}.declared_complete", coverage.get("accounting_complete"), True)
        checks.add(f"coverage.{key}.skip_reasons", all(str(row.get("reason", "")).strip() for row in skips))
        checks.add(
            f"coverage.{key}.development_active", any(timestamp <= expected.development_end for timestamp in rebalances)
        )
        checks.add(
            f"coverage.{key}.validation_active", any(timestamp >= expected.validation_start for timestamp in rebalances)
        )
        checks.equal(
            f"coverage.{key}.development_count",
            coverage.get("development_decisions"),
            sum(timestamp <= expected.development_end for timestamp in rebalances),
        )
        checks.equal(
            f"coverage.{key}.validation_count",
            coverage.get("validation_decisions"),
            sum(timestamp >= expected.validation_start for timestamp in rebalances),
        )


def _verify_execution(
    checks: _Checks,
    audits: Mapping[str, Mapping[str, Any]],
    bars: Mapping[str, Mapping[datetime, Mapping[str, float]]],
    lifetimes: Mapping[str, Mapping[str, str]],
    expected: VerificationExpectations,
) -> None:
    for key, audit in audits.items():
        multiplier = int(audit.get("cost_multiplier", 0))
        fill_slippage = fill_spread = fill_fees = 0.0
        cash = _finite(audit["starting_equity"])
        decisions = _rebalance_map(audit)
        for index, fill in enumerate(audit.get("fills", [])):
            label = f"execution.{key}.fill_{index}"
            timestamp = _timestamp(fill["timestamp"])
            decision = _timestamp(fill["decision_timestamp"])
            symbol = str(fill["symbol"])
            side = str(fill["side"]).lower()
            qty = _finite(fill["qty"])
            reference = _finite(fill["reference_price"])
            fill_price = _finite(fill["fill_price"])
            fee = _finite(fill["fee"])
            checks.add(f"{label}.known_decision", decision in decisions)
            checks.add(f"{label}.next_bar_only", timestamp > decision)
            raw = bars.get(symbol, {}).get(timestamp)
            checks.add(f"{label}.real_bar", raw is not None)
            if raw is not None:
                checks.close(f"{label}.reference_open", reference, raw["open"])
            slippage = qty * reference * expected.base_slippage_bps * multiplier / 10_000.0
            spread = qty * reference * expected.base_spread_bps * multiplier / 2.0 / 10_000.0
            direction = 1.0 if side == "buy" else -1.0
            expected_price = reference * (
                1.0 + direction * (expected.base_slippage_bps + expected.base_spread_bps / 2.0) * multiplier / 10_000.0
            )
            checks.close(f"{label}.slippage", _finite(fill["slippage_cost"]), slippage)
            checks.close(f"{label}.spread", _finite(fill["spread_cost"]), spread)
            checks.close(f"{label}.adverse_fill_price", fill_price, expected_price)
            checks.add(f"{label}.side", side in {"buy", "sell"})
            checks.add(f"{label}.quantity", qty > 0)
            checks.add(f"{label}.fee", fee >= 0)
            if side == "buy":
                cash -= qty * fill_price + fee
            elif side == "sell":
                cash += qty * fill_price - fee
            fill_slippage += _finite(fill["slippage_cost"])
            fill_spread += _finite(fill["spread_cost"])
            fill_fees += fee
            if symbol == "SH":
                checks.add(f"{label}.real_sh_not_synthetic", raw is not None and raw["volume"] > 0)

        terminal_slippage = terminal_spread = terminal_fees = 0.0
        for index, event in enumerate(audit.get("terminal_events", [])):
            label = f"terminal.{key}.{index}"
            timestamp = _timestamp(event["timestamp"])
            symbol = str(event["symbol"])
            event_class = str(event.get("terminal_event_class", ""))
            raw = bars.get(symbol, {}).get(timestamp)
            checks.add(f"{label}.real_last_bar", raw is not None)
            if raw is not None:
                checks.close(f"{label}.last_close", _finite(event["last_close"]), raw["close"])
            checks.add(f"{label}.removed", event.get("position_removed") is True)
            checks.add(f"{label}.quantity", _finite(event["quantity"]) > 0)
            checks.add(f"{label}.proceeds", _finite(event["proceeds"]) >= 0)
            policy = str(event.get("effective_policy", event.get("policy", "")))
            checks.add(f"{label}.policy", policy in {"FINAL_BAR", "TERMINAL_BAR"})
            if policy == "TERMINAL_BAR":
                lifetime = lifetimes.get(symbol, {})
                checks.equal(f"{label}.lifetime_class", lifetime.get("terminal_event_class"), event_class)
                checks.equal(
                    f"{label}.lifetime_timestamp",
                    _timestamp(lifetime.get("last_observed_bar")),
                    timestamp,
                )
            cash += _finite(event["proceeds"])
            terminal_slippage += _finite(event["slippage_cost"])
            terminal_spread += _finite(event["spread_cost"])
            terminal_fees += _finite(event["fee"])

        dividends = _finite(audit.get("dividend_total", 0.0))
        cash += dividends
        checks.close(
            f"costs.{key}.slippage_sum", _finite(audit["slippage_cost_total"]), fill_slippage + terminal_slippage
        )
        checks.close(f"costs.{key}.spread_sum", _finite(audit["spread_cost_total"]), fill_spread + terminal_spread)
        checks.close(f"costs.{key}.fee_sum", _finite(audit["fee_total"]), fill_fees + terminal_fees)
        checks.add(f"state.{key}.holdings_empty", not audit.get("state_holdings"))
        checks.add(f"state.{key}.pending_orders_empty", not audit.get("state_pending_orders"))
        checks.close(f"nav.{key}.cash_flow", cash, _finite(audit["state_cash"]))
        checks.close(f"nav.{key}.ending_cash", _finite(audit["ending_equity"]), _finite(audit["state_cash"]))
        curve = audit.get("curve", [])
        checks.add(f"nav.{key}.curve_present", bool(curve))
        if curve:
            checks.close(f"nav.{key}.curve_final", _finite(curve[-1][1]), _finite(audit["ending_equity"]))


def verify_campaign(
    output_dir: Path,
    repo_root: Path,
    *,
    expectations: VerificationExpectations | None = None,
    git_reader: Callable[..., str] = _git,
) -> dict[str, Any]:
    """Verify replay artifacts and always persist ``independent_verification.json``."""

    output_dir = Path(output_dir)
    repo_root = Path(repo_root)
    expected = expectations or VerificationExpectations()
    checks = _Checks()
    try:
        manifest = _json(output_dir / "run_manifest.json")
        results = _json(output_dir / "campaign_results.json")
        audits = _json(output_dir / "execution_audit.json")
        checks.equal("schema.run_keys.audit", set(audits), EXPECTED_RUN_KEYS)
        checks.equal("schema.run_keys.results", set(results.get("runs", {})), EXPECTED_RUN_KEYS)

        wanted_symbols = {"SPY", "SH"}
        for audit in audits.values():
            wanted_symbols.update(str(fill["symbol"]) for fill in audit.get("fills", []))
            wanted_symbols.update(str(event["symbol"]) for event in audit.get("terminal_events", []))
        bars = _load_bars(repo_root / BAR_PATH, wanted_symbols)
        lifetimes = _load_lifetimes(repo_root / LIFETIME_PATH)

        _verify_identity(checks, repo_root, manifest, results, expected, git_reader)
        _verify_data(checks, bars, lifetimes)
        if set(audits) == EXPECTED_RUN_KEYS and set(results.get("runs", {})) == EXPECTED_RUN_KEYS:
            _verify_signal_and_semantics(checks, audits, results, bars["SPY"], bars["SH"])
            _verify_allocations(checks, audits)
            _verify_coverage(checks, audits, results, bars["SPY"], expected)
            _verify_execution(checks, audits, bars, lifetimes, expected)
    except Exception as exc:  # fail closed and leave a machine-readable report
        checks.add("verification.completed_without_exception", False, f"{type(exc).__name__}: {exc}")

    failed = [row for row in checks.rows if row["status"] == "FAIL"]
    payload = {
        "campaign": expected.campaign,
        "verifier": "independent_spy_defensive_overlay_v1",
        "status": "PASS" if checks.rows and not failed else "FAIL",
        "summary": {"checks": len(checks.rows), "passed": len(checks.rows) - len(failed), "failed": len(failed)},
        "checks": checks.rows,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "independent_verification.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    return 0 if verify_campaign(args.output_dir, args.repo_root)["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
