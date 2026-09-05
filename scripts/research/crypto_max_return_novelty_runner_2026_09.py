"""Pre-economic C3 novelty runner; explicit manifest/receipt paths only."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import statistics
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def verify_selected_inputs(manifest_path: Path, receipt_path: Path) -> dict[str, Any]:
    """Fail closed on explicit selected files; no R1/R2/R3 fallback."""
    if not manifest_path.is_file() or not receipt_path.is_file():
        raise RuntimeError("BLOCKED_SELECTED_INPUT_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if manifest.get("status") not in {"PRE_NOVELTY_PENDING_REVIEW", "FROZEN_NOVELTY"}:
        raise RuntimeError("BLOCKED_NOVELTY_MANIFEST_STATUS")
    expected_source = manifest.get("source", {}).get("file_sha256", {})
    if not isinstance(expected_source, Mapping):
        raise RuntimeError("BLOCKED_SOURCE_PINS_MISSING")
    for relative, expected in expected_source.items():
        actual = sha256_file(REPO / str(relative))
        if actual != expected:
            raise RuntimeError(f"BLOCKED_SOURCE_HASH:{relative}")
    if receipt.get("manifest_sha256") != manifest.get("manifest_sha256"):
        raise RuntimeError("BLOCKED_RECEIPT_MANIFEST_MISMATCH")
    observed = receipt.get("observed_sha256")
    expected_inputs = manifest.get("inputs", {})
    expected_hashes = {
        key: value.get("sha256")
        for key, value in expected_inputs.items()
        if isinstance(value, Mapping) and value.get("sha256")
    }
    if not isinstance(observed, Mapping) or any(observed.get(key) != value for key, value in expected_hashes.items()):
        raise RuntimeError("BLOCKED_RECEIPT_INPUT_HASH_MISMATCH")
    return manifest


def rank_correlation(a: Mapping[str, int], b: Mapping[str, int]) -> float | None:
    common = sorted(set(a) & set(b))
    if len(common) < 2:
        return None
    av = [a[symbol] for symbol in common]
    bv = [b[symbol] for symbol in common]
    ma, mb = statistics.fmean(av), statistics.fmean(bv)
    den_a = math.sqrt(sum((value - ma) ** 2 for value in av))
    den_b = math.sqrt(sum((value - mb) ** 2 for value in bv))
    if den_a == 0 or den_b == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(av, bv, strict=True)) / (den_a * den_b)


def novelty_summary(traces: list[Mapping[str, object]], incumbent_traces: list[Mapping[str, object]]) -> dict[str, object]:
    """Compare raw scores on the same snapshots; this is not economic output."""
    rows = []
    if len(traces) != len(incumbent_traces):
        raise RuntimeError("BLOCKED_PAIRED_TRACE_LENGTH_MISMATCH")
    for candidate, incumbent in zip(traces, incumbent_traces, strict=True):
        ranks = candidate.get("ranks", {})
        incumbent_ranks = incumbent.get("ranks", {})
        if isinstance(ranks, Mapping) and isinstance(incumbent_ranks, Mapping):
            corr = rank_correlation(ranks, incumbent_ranks)
            c_top = tuple(sorted(ranks, key=lambda symbol: ranks[symbol])[:2])
            i_top = tuple(sorted(incumbent_ranks, key=lambda symbol: incumbent_ranks[symbol])[:2])
            c_target = set(candidate.get("target_symbols", ()))
            i_target = set(incumbent.get("target_symbols", ()))
            rows.append({"decision_timestamp": candidate.get("decision_timestamp"), "spearman": corr, "identical_top2": c_top == i_top, "target_jaccard": len(c_target & i_target) / len(c_target | i_target) if c_target | i_target else 1.0})
    correlations = [row["spearman"] for row in rows if row["spearman"] is not None]
    valid_pair_count = len(correlations)
    identical_top2_fraction = statistics.fmean(bool(row["identical_top2"]) for row in rows if row["spearman"] is not None) if valid_pair_count else None
    return {
        "status": "NOVELTY_METRICS_ONLY",
        "decision_count": len(rows),
        "paired_snapshot_count": len(rows),
        "valid_rank_correlation_count": valid_pair_count,
        "undefined_rank_correlation_count": len(rows) - valid_pair_count,
        "median_rank_correlation": statistics.median(correlations) if correlations else None,
        "identical_top2_fraction": identical_top2_fraction,
        "median_hysteresis_target_jaccard": statistics.median(row["target_jaccard"] for row in rows) if rows else None,
        "near_duplicate_rule": "median_rank_correlation >= 0.95 AND identical_top2_fraction >= 0.90 => NO_ECONOMIC_RUN_NEAR_DUPLICATE",
        "near_duplicate_status": ("BLOCKED_INSUFFICIENT_PAIRED_SNAPSHOTS" if valid_pair_count < 30 else ("NO_ECONOMIC_RUN_NEAR_DUPLICATE" if (correlations and statistics.median(correlations) >= 0.95 and identical_top2_fraction >= 0.90) else "NOVELTY_GATE_OPEN")),
        "rows": rows,
        "economic_run": False,
    }


def replay_decisions(candidate_contexts: Sequence[object], incumbent_contexts: Sequence[object]) -> dict[str, object]:
    """Observation-only replay; contexts must be built by the native PIT seam.

    No engine, fills, equity curve, costs, or PnL are created here. Holdings are
    virtual previous targets solely to exercise the inherited hysteresis rule.
    """
    from dataclasses import replace
    from scripts.research.crypto_max_return_novelty_2026_09 import CryptoMaxReturnHysteresisResearch
    from parayabuck.portfolio.crypto_low_variance_hysteresis import CryptoLowVarianceHysteresis1dV1

    candidate = CryptoMaxReturnHysteresisResearch({"top_n": 2, "rebalance_hours": 96})
    incumbent = CryptoLowVarianceHysteresis1dV1({"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 96})
    candidate_traces: list[Mapping[str, object]] = []
    incumbent_traces: list[Mapping[str, object]] = []
    candidate_holdings: dict[str, float] = {}
    incumbent_holdings: dict[str, float] = {}
    for candidate_context, incumbent_context in zip(candidate_contexts, incumbent_contexts, strict=True):
        candidate_portfolio = candidate.decide(replace(candidate_context, holdings=dict(candidate_holdings)))
        incumbent_scores = incumbent._score_candidates(incumbent_context)
        incumbent_portfolio = incumbent.decide(replace(incumbent_context, holdings=dict(incumbent_holdings)))
        candidate_holdings = {weight.symbol: weight.weight for weight in candidate_portfolio.weights}
        incumbent_holdings = {weight.symbol: weight.weight for weight in incumbent_portfolio.weights}
        candidate_traces.append(candidate.decision_traces[-1])
        incumbent_traces.append({"ranks": {symbol: index for index, (_score, symbol) in enumerate(incumbent_scores, start=1)}, "target_symbols": tuple(sorted(incumbent_holdings)), "decision_timestamp": incumbent_context.decision_timestamp})
    return novelty_summary(candidate_traces, incumbent_traces)


def run_native_novelty(manifest_path: Path, receipt_path: Path, *, output_path: Path) -> dict[str, object]:
    """Build causal native contexts and replay only strategy decisions."""
    manifest = verify_selected_inputs(manifest_path, receipt_path)
    if manifest.get("status") != "FROZEN_NOVELTY":
        raise RuntimeError("BLOCKED_NOVELTY_NOT_FROZEN")
    from parayabuck.backtest.product import build_product_universe, load_csv_bars
    from parayabuck.config import load_config
    from parayabuck.core.models import Market
    from parayabuck.data.aggregator import aggregate_historical_bars
    from parayabuck.portfolio.base import PortfolioContext
    from scripts.research.crypto_max_return_novelty_2026_09 import CryptoMaxReturnHysteresisResearch
    from parayabuck.portfolio.crypto_low_variance_hysteresis import CryptoLowVarianceHysteresis1dV1

    inputs = manifest.get("inputs", {})
    record = inputs.get("bars_1h", inputs.get("bars")) if isinstance(inputs, Mapping) else None
    if not isinstance(record, Mapping) or not record.get("path"):
        raise RuntimeError("BLOCKED_CRYPTO_DATASET_PIN_MISSING")
    config = load_config(str(REPO / "config/settings.yaml"))
    bars_1h = load_csv_bars(REPO / str(record["path"]), default_market=Market.CRYPTO)
    bars_1d = aggregate_historical_bars(bars_1h, "1d", require_complete=True)
    evaluation = manifest.get("evaluation", {})
    if not isinstance(evaluation, Mapping) or not isinstance(evaluation.get("full"), Mapping):
        raise RuntimeError("BLOCKED_NOVELTY_WINDOW_MISSING")
    start = datetime.fromisoformat(str(evaluation["full"]["start"]).replace("Z", "+00:00"))
    end = datetime.fromisoformat(str(evaluation["full"]["end"]).replace("Z", "+00:00"))
    # Scanner remains the configured product scanner and supplies the PIT
    # eligible pool. The candidate does not redefine it.
    universe = build_product_universe(config, bars_1h, market=Market.CRYPTO, end=end, timeframe="1h")
    grouped: dict[str, list[object]] = {}
    for bar in bars_1d:
        if bar.timestamp <= end:
            grouped.setdefault(bar.symbol, []).append(bar)
    for values in grouped.values():
        values.sort(key=lambda bar: bar.timestamp)
    # Fixed elapsed-time schedule anchored to the declared window start. A
    # missing completed day never shifts later scheduled timestamps. Schedule
    # membership is independent of whether that day's bucket exists in data.
    schedule: list[datetime] = []
    scheduled = start
    while scheduled <= end:
        schedule.append(scheduled)
        scheduled += timedelta(hours=96)
    candidate = CryptoMaxReturnHysteresisResearch({"top_n": 2, "rebalance_hours": 96})
    incumbent = CryptoLowVarianceHysteresis1dV1({"top_n": 2, "rebalance_hours": 96, "variance_lookback_days": 22})
    candidate_held: dict[str, float] = {}
    incumbent_held: dict[str, float] = {}
    candidate_traces: list[Mapping[str, object]] = []
    incumbent_traces: list[Mapping[str, object]] = []

    def snapshot_at(timestamp: datetime):
        snapshots = universe.snapshots
        index = bisect.bisect_right([item.effective_at for item in snapshots], timestamp) - 1
        return snapshots[index] if index >= 0 else None

    def context_at(timestamp: datetime, symbols: Sequence[str], holdings: Mapping[str, float], snapshot: object) -> PortfolioContext:
        # Bar timestamps are bucket starts. The due timestamp is the real
        # wall-clock as-of; only buckets whose end is already <= due are
        # visible. Never add a day to the cutoff: that would expose the due
        # day's close and make scores change when future intraday bars arrive.
        cutoff = timestamp
        history = {
            symbol: tuple(bar for bar in grouped.get(symbol, ()) if bar.timestamp + timedelta(days=1) <= cutoff)
            for symbol in symbols
        }
        prices = {symbol: values[-1].close for symbol, values in history.items() if values}
        snapshot_key = canonical_digest({"effective_at": snapshot.effective_at.isoformat(), "symbols": sorted(snapshot.symbols)})
        return PortfolioContext(timestamp, Market.CRYPTO, "1d", history, universe, snapshot_key, 1.0, dict(holdings), prices, "BTC/USD")

    skipped_schedule_reasons: list[dict[str, object]] = []
    for timestamp in schedule:
        snapshot = snapshot_at(timestamp)
        if snapshot is None:
            skipped_schedule_reasons.append({"decision_timestamp": timestamp.isoformat(), "reason": "no_pit_snapshot"})
            continue
        full_symbols = tuple(sorted(snapshot.symbols))
        full_context = context_at(timestamp, full_symbols, {}, snapshot)
        candidate_scores = candidate._score_candidates(full_context)
        incumbent_scores = incumbent._score_candidates(full_context)
        candidate_observed = tuple(symbol for _score, symbol in candidate_scores[: snapshot.maximum_stream_symbols])
        incumbent_observed = tuple(symbol for _score, symbol in incumbent_scores[: snapshot.maximum_stream_symbols])
        candidate_context = context_at(timestamp, candidate_observed, candidate_held, snapshot)
        incumbent_context = context_at(timestamp, incumbent_observed, incumbent_held, snapshot)
        if candidate_context.decision_timestamp != incumbent_context.decision_timestamp or candidate_context.universe_version != incumbent_context.universe_version:
            raise RuntimeError("BLOCKED_CONTEXT_TIMESTAMP_OR_SNAPSHOT_MISMATCH")
        incumbent_previous_held = tuple(sorted(symbol for symbol, weight in incumbent_held.items() if weight > 1e-12))
        candidate_portfolio = candidate.decide(candidate_context)
        incumbent_portfolio = incumbent.decide(incumbent_context)
        candidate_held = {weight.symbol: weight.weight for weight in candidate_portfolio.weights}
        incumbent_held = {weight.symbol: weight.weight for weight in incumbent_portfolio.weights}
        incumbent_rank_map = {symbol: index for index, (_score, symbol) in enumerate(incumbent_scores, start=1)}
        incumbent_target_symbols = tuple(sorted(incumbent_held))
        incumbent_retained = tuple(
            sorted(
                symbol
                for symbol in incumbent_previous_held
                if symbol in incumbent_target_symbols
                and incumbent_rank_map.get(symbol, 999) <= incumbent.RETENTION_RANK
                and incumbent_context.in_universe(symbol)
            )
        )
        incumbent_scores_by_symbol = {symbol: score for score, symbol in incumbent_scores}
        incumbent_trace = {
            "decision_timestamp": timestamp,
            "candidate_scores": {
                symbol: {
                    "eligible": incumbent_context.in_universe(symbol),
                    "score": incumbent_scores_by_symbol.get(symbol),
                    "reason": "eligible_and_complete" if symbol in incumbent_scores_by_symbol else "insufficient_or_nonconsecutive",
                }
                for symbol in sorted(incumbent_context.history)
            },
            "eligible_symbols": sorted(symbol for symbol in incumbent_context.history if incumbent_context.in_universe(symbol)),
            "observed_symbols": sorted(incumbent_context.history),
            "previous_held": incumbent_previous_held,
            "retained": incumbent_retained,
            "retention_reason": "derived from prev-held/targets and frozen HYST4 rule",
            "retention_rank": incumbent.RETENTION_RANK,
            "target_symbols": incumbent_target_symbols,
        }
        candidate_trace = dict(candidate.decision_traces[-1])
        candidate_trace["ranks"] = {symbol: index for index, (_score, symbol) in enumerate(candidate_scores, start=1)}
        incumbent_trace["ranks"] = incumbent_rank_map
        candidate_trace["full_eligible_symbols"] = full_symbols; candidate_trace["observer_symbols"] = candidate_observed
        incumbent_trace["full_eligible_symbols"] = full_symbols; incumbent_trace["observer_symbols"] = incumbent_observed
        candidate_traces.append(candidate_trace); incumbent_traces.append(incumbent_trace)
    result = novelty_summary(candidate_traces, incumbent_traces)
    result.update({"manifest_sha256": manifest.get("manifest_sha256"), "economic_run": False, "engine_run": False, "virtual_holdings_only": True, "observer_cap": 12, "schedule_policy": "fixed UTC 96h steps anchored at full-window start", "scheduled_timestamp_count": len(schedule), "skipped_schedule_reasons": skipped_schedule_reasons, "candidate_traces": candidate_traces, "incumbent_traces": incumbent_traces})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--validate-inputs", action="store_true")
    parser.add_argument("--run", action="store_true", help="reserved; native contexts are supplied by the reviewed orchestrator")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.run:
        if args.output is None:
            parser.error("--run requires --output")
        result = run_native_novelty(args.manifest, args.receipt, output_path=args.output)
        print(json.dumps({"status": result["status"], "output": str(args.output)}, sort_keys=True))
    if args.validate_inputs:
        manifest = verify_selected_inputs(args.manifest, args.receipt)
        print(json.dumps({"status": "INPUTS_VERIFIED_NO_RUN", "manifest_sha256": manifest.get("manifest_sha256")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
