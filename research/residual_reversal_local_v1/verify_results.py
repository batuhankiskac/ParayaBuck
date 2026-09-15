"""Independently verify the residual-reversal campaign artifacts.

This process deliberately re-reads the source panel and result JSON rather
than trusting the campaign runner's in-memory counters.  It checks data
identity, frozen signal semantics, execution ordering/cost arithmetic, and
the accounting identities needed before the research verdict is considered
usable.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.equity_residual_momentum import (
    EquityResidualMomentum1dV2,
    _aligned_returns,
)

try:
    from .candidate import EquityShortTermResidualReversalV1, frozen_reversal_parameters
except ImportError:  # pragma: no cover - allows direct script execution
    from candidate import EquityShortTermResidualReversalV1, frozen_reversal_parameters


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(raw: object) -> datetime:
    value = str(raw).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


class Verification:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(self, name: str, condition: bool, details: Any = None) -> None:
        row = {"name": name, "status": "PASS" if condition else "FAIL"}
        if details is not None:
            row["details"] = details
        self.checks.append(row)

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [row for row in self.checks if row["status"] == "FAIL"]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_source_panel(verifier: Verification, receipt: dict[str, Any], manifest: dict[str, Any]) -> None:
    path = Path(str(receipt["source_path"]))
    verifier.check("source panel exists", path.is_file(), str(path))
    if not path.is_file():
        return
    verifier.check("source panel SHA-256", _hash_file(path) == receipt["source_sha256"])
    counts = {"rows": 0, "normalized": 0, "invalid": 0, "duplicates": 0, "out_of_order": 0}
    symbols: set[str] = set()
    dates: set[str] = set()
    spy_dates: set[str] = set()
    previous_timestamp: datetime | None = None
    previous_symbol: str | None = None
    symbols_at_timestamp: set[str] = set()
    first_valid: datetime | None = None
    last_valid: datetime | None = None
    same_timestamp_resets = 0
    timeframe_counts: dict[str, int] = {}
    market_counts: dict[str, int] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            counts["rows"] += 1
            timestamp = _timestamp(raw["timestamp"])
            symbol = str(raw["symbol"]).strip().upper()
            if previous_timestamp is not None and timestamp < previous_timestamp:
                counts["out_of_order"] += 1
            if previous_timestamp != timestamp:
                symbols_at_timestamp = set()
                previous_symbol = None
            elif symbol in symbols_at_timestamp:
                    counts["duplicates"] += 1
            elif previous_symbol is not None and symbol < previous_symbol:
                same_timestamp_resets += 1
            symbols_at_timestamp.add(symbol)
            previous_timestamp = timestamp
            previous_symbol = symbol
            raw_timeframe = str(raw["timeframe"]).strip()
            raw_market = str(raw["market"]).strip()
            timeframe_counts[raw_timeframe] = timeframe_counts.get(raw_timeframe, 0) + 1
            market_counts[raw_market] = market_counts.get(raw_market, 0) + 1
            if raw_timeframe.lower() not in {"1d", "1day"} or raw_market.lower() != "equity":
                continue
            try:
                values = [float(str(raw[field]).strip()) for field in ("open", "high", "low", "close", "volume")]
            except (TypeError, ValueError):
                counts["invalid"] += 1
                continue
            if all(value == 0.0 for value in values):
                continue
            open_price, high, low, close, volume = values
            valid = (
                all(math.isfinite(value) for value in values)
                and open_price > 0
                and high > 0
                and low > 0
                and close > 0
                and volume >= 0
                and high >= max(open_price, close)
                and low <= min(open_price, close)
            )
            if not valid:
                counts["invalid"] += 1
                continue
            counts["normalized"] += 1
            symbols.add(symbol)
            date_key = timestamp.date().isoformat()
            dates.add(date_key)
            if symbol == "SPY":
                spy_dates.add(date_key)
            first_valid = timestamp if first_valid is None else min(first_valid, timestamp)
            last_valid = timestamp if last_valid is None else max(last_valid, timestamp)
    normalization = receipt["normalization"]
    verifier.check("source row count", counts["rows"] == receipt["source_rows"], counts)
    verifier.check("source normalized row count", counts["normalized"] == receipt["normalized_rows"], counts)
    verifier.check("source positive OHLC validity", counts["invalid"] == normalization["invalid_positive_ohlc_rows"], counts["invalid"])
    verifier.check("source duplicate/order invariants", counts["duplicates"] == 0 and counts["out_of_order"] == 0 and same_timestamp_resets == normalization["same_timestamp_symbol_resets"], {**counts, "same_timestamp_resets": same_timestamp_resets})
    verifier.check("source symbol/date counts", len(symbols) == receipt["unique_symbols"] and len(dates) == receipt["unique_dates"], {"symbols": len(symbols), "dates": len(dates)})
    spy = receipt["spy_coverage"]
    verifier.check("SPY coverage receipt", len(spy_dates) == spy["valid_rows"] and (min(spy_dates) if spy_dates else None) == spy["first_date"] and (max(spy_dates) if spy_dates else None) == spy["last_date"], {"rows": len(spy_dates), "first": min(spy_dates) if spy_dates else None, "last": max(spy_dates) if spy_dates else None})
    verifier.check("source timeframe/market normalization", timeframe_counts == receipt["source_timeframe_counts"] and market_counts == receipt["source_market_counts"])
    period = receipt["actual_valid_bar_period"]
    verifier.check("source valid period receipt", first_valid and last_valid and first_valid.isoformat() == period["start"] and last_valid.isoformat() == period["end"])
    verifier.check("manifest data identity", manifest["data_source_sha256"] == receipt["source_sha256"])


def _verify_frozen_signal(verifier: Verification) -> None:
    candidate = EquityShortTermResidualReversalV1(frozen_reversal_parameters())
    expected = (0.99**21) - 1.0
    inside = [-0.01] * 21
    score = candidate._candidate_score([9.0, *inside], formation=21, skip=0)
    score_with_different_outside = candidate._candidate_score([-9.0, *inside], formation=21, skip=0)
    verifier.check("frozen parameter tuple", candidate.parameters == frozen_reversal_parameters(), candidate.parameters)
    verifier.check("exact 21 completed returns and zero skip", abs(score - expected) < 1e-12 and score == score_with_different_outside, {"score": score, "expected": expected})
    verifier.check("no positive-score gate", candidate._candidate_passes_gate([], -0.25, formation=21, skip=0))

    original = EquityResidualMomentum1dV2._score_candidates

    def stub(_self: object, _context: object) -> tuple[tuple[tuple[float, str], ...], dict[str, float]]:
        return ((0.10, "POSITIVE"), (-0.20, "NEGATIVE")), {"POSITIVE": 1.0, "NEGATIVE": 1.0}

    EquityResidualMomentum1dV2._score_candidates = stub  # type: ignore[method-assign]
    try:
        ranked, _vols = candidate._score_candidates(None)  # type: ignore[arg-type]
    finally:
        EquityResidualMomentum1dV2._score_candidates = original  # type: ignore[method-assign]
    verifier.check("negative residual performers rank first", ranked[0] == (-0.20, "NEGATIVE"), ranked)

    base = datetime(2024, 1, 2, tzinfo=UTC)
    stock = tuple(
        Bar(base + timedelta(days=offset), "ABC", "1d", close, close, close, close, 1_000, Market.EQUITY)
        for offset, close in ((0, 100.0), (2, 90.0), (3, 99.0))
    )
    market = {
        base + timedelta(days=offset): close
        for offset, close in ((0, 100.0), (1, 101.0), (2, 102.0), (3, 103.0))
    }
    aligned = _aligned_returns(stock, market, 3)
    expected_stock = [90.0 / 100.0 - 1.0, 99.0 / 90.0 - 1.0]
    expected_market = [102.0 / 100.0 - 1.0, 103.0 / 102.0 - 1.0]
    verifier.check("timestamp-inner-join residual alignment", aligned is not None and all(abs(a - b) < 1e-12 for a, b in zip(aligned[0], expected_stock, strict=True)) and all(abs(a - b) < 1e-12 for a, b in zip(aligned[1], expected_market, strict=True)), aligned)


def _verify_run(verifier: Verification, run_name: str, cost: int, summary: dict[str, Any], audit: dict[str, Any]) -> None:
    curve = audit.get("curve", [])
    curve_timestamps = [_timestamp(row[0]) for row in curve]
    curve_values = [float(row[1]) for row in curve]
    strictly_increasing = all(left < right for left, right in zip(curve_timestamps, curve_timestamps[1:], strict=False))
    finite_curve = all(math.isfinite(value) for value in curve_values)
    label = f"{run_name} {cost}x"
    verifier.check(f"{label} curve ordering/finite NAV", bool(curve) and strictly_increasing and finite_curve)
    if not curve:
        return
    verifier.check(f"{label} ending NAV identity", abs(float(audit["ending_equity"]) - curve_values[-1]) < 1e-7)
    expected_return = curve_values[-1] / float(audit["starting_equity"]) - 1.0
    verifier.check(f"{label} return identity", abs(float(audit["total_return"]) - expected_return) < 1e-10 and abs(float(summary["total_return"]) - expected_return) < 1e-10)
    verifier.check(f"{label} summary identity", abs(float(summary["ending_equity"]) - curve_values[-1]) < 1e-7 and summary["fill_count"] == len(audit.get("fills", [])) and summary["rebalanced_decision_count"] == len(audit.get("rebalances", [])))

    fills = audit.get("fills", [])
    cost_bps = 2.0 * cost
    spread_bps = 2.0 * cost
    adverse = (cost_bps + spread_bps / 2.0) / 10_000.0
    fill_order_ok = True
    fill_cost_ok = True
    for fill in fills:
        decision = _timestamp(fill["decision_timestamp"])
        filled = _timestamp(fill["timestamp"])
        fill_order_ok = fill_order_ok and decision < filled
        reference = float(fill["reference_price"])
        expected_price = reference * (1.0 + adverse if fill["side"] == "buy" else 1.0 - adverse)
        fill_cost_ok = fill_cost_ok and abs(float(fill["fill_price"]) - expected_price) <= 1e-10 * max(1.0, abs(expected_price))
        fill_cost_ok = fill_cost_ok and abs(float(fill["slippage_cost"]) - float(fill["qty"]) * reference * cost_bps / 10_000.0) <= 1e-10 * max(1.0, abs(reference))
        fill_cost_ok = fill_cost_ok and abs(float(fill["spread_cost"]) - float(fill["qty"]) * reference * spread_bps / 2.0 / 10_000.0) <= 1e-10 * max(1.0, abs(reference))
        fill_cost_ok = fill_cost_ok and all(_finite(fill[key]) and float(fill[key]) >= 0 for key in ("qty", "slippage_cost", "spread_cost", "fee"))
    verifier.check(f"{label} next-bar fill ordering", fill_order_ok)
    verifier.check(f"{label} fill cost arithmetic", fill_cost_ok)

    rebalances = audit.get("rebalances", [])
    decisions = [_timestamp(record["decision_timestamp"]) for record in rebalances]
    weekly = [timestamp.isocalendar()[:2] for timestamp in decisions]
    target_limits_ok = True
    for record in rebalances:
        target_weights = [float(pair[1]) for pair in record["target_weights"]]
        target_limits_ok = target_limits_ok and all(0.0 <= weight <= 0.10 + 1e-8 for weight in target_weights)
        target_limits_ok = target_limits_ok and sum(target_weights) <= 0.55 + 1e-8
        target_limits_ok = target_limits_ok and 0.0 <= float(record["target_cash_weight"]) <= 1.0
    verifier.check(f"{label} weekly decision cadence", len(weekly) == len(set(weekly)))
    verifier.check(f"{label} target caps and sleeve allocation", target_limits_ok)
    verifier.check(f"{label} non-negative cash", _finite(audit.get("state_cash")) and float(audit["state_cash"]) >= -1e-7)
    holdings = {str(symbol): float(quantity) for symbol, quantity in dict(audit.get("state_holdings", {})).items()}
    prices = {str(symbol): float(price) for symbol, price in dict(audit.get("state_last_prices", {})).items()}
    nav = float(audit["state_cash"]) + sum(quantity * prices[symbol] for symbol, quantity in holdings.items() if symbol in prices)
    verifier.check(f"{label} final state NAV identity", abs(nav - curve_values[-1]) <= 1e-6 * max(1.0, abs(curve_values[-1])))
    verifier.check(f"{label} realized concentration metric finite", _finite(summary["maximum_concentration"]) and float(summary["maximum_concentration"]) >= 0.0)


def _verify_outputs(verifier: Verification, output: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    audit = _load_json(output / "execution_audit.json")
    files = {
        "v3": "v3_control.json",
        "reversal": "standalone_reversal.json",
        "treatment": "treatment.json",
    }
    payloads: dict[str, Any] = {}
    for name, filename in files.items():
        path = output / filename
        verifier.check(f"{filename} exists", path.is_file())
        if path.is_file():
            payloads[name] = _load_json(path)
    for name, payload in payloads.items():
        for cost in (1, 2, 3):
            record = payload["costs"][str(cost)]
            _verify_run(verifier, name, cost, record["full"], audit[f"{name}_{cost}x"])
            validation = record["validation"]
            start = _timestamp(validation["start"])
            end = _timestamp(validation["end"])
            verifier.check(f"{name} {cost}x validation boundary", start >= _timestamp(manifest["windows"]["validation_start"]) and end <= _timestamp(manifest["windows"]["validation_end"]))
    verifier.check("fresh V3 control identity", payloads.get("v3", {}).get("strategy") == "equity_residual_momentum_v3")
    verifier.check("fresh reversal identity", payloads.get("reversal", {}).get("strategy") == "equity_short_term_residual_reversal_v1")
    verifier.check("treatment allocation receipt", manifest["treatment_allocation"] == {"v3": 0.4125, "reversal": 0.1375, "total": 0.55, "cash_remainder": 0.45})
    return payloads


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    repo_root = output.parents[1]
    verifier = Verification()
    manifest = _load_json(output / "run_manifest.json")
    receipt = _load_json(output / "DATA_RECEIPT.json")
    protocol_path = output / "FROZEN_PROTOCOL.md"
    baseline_path = output / "CURRENT_BASELINE.md"
    verifier.check("protocol hash", _hash_file(protocol_path) == manifest["protocol_sha256"])
    verifier.check("baseline hash", _hash_file(baseline_path) == manifest["baseline_sha256"])
    production_source = Path(str(manifest["production_source"]))
    if not production_source.is_absolute():
        production_source = repo_root / production_source
    verifier.check("production V3 source hash", _hash_file(production_source) == manifest["production_source_sha256"])
    lifetime_path = Path(str(receipt["lifetime_table"]))
    action_path = Path(str(receipt["corporate_actions"]["source_path"]))
    verifier.check("lifetime table hash", lifetime_path.is_file() and _hash_file(lifetime_path) == manifest["lifetime_table_sha256"])
    verifier.check("corporate-action hash", action_path.is_file() and _hash_file(action_path) == manifest["corporate_actions_sha256"])
    verifier.check("main branch identity", subprocess.check_output(["git", "rev-parse", "main"], cwd=repo_root, text=True).strip() == manifest["main_sha"])
    _verify_source_panel(verifier, receipt, manifest)
    _verify_frozen_signal(verifier)
    _verify_outputs(verifier, output, manifest)
    payload = {
        "status": "PASS" if not verifier.failed else "FAIL",
        "checks": verifier.checks,
        "failure_count": len(verifier.failed),
        "verifier": "research/residual_reversal_local_v1/verify_results.py",
    }
    (output / "independent_verification.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for row in verifier.failed:
        print(f"FAIL: {row['name']}: {row.get('details', '')}")
    print(f"Independent verification: {payload['status']} ({len(verifier.checks)} checks)")
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
