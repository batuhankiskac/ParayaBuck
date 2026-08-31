#!/usr/bin/env python3
"""Run the candidate-owned observation-basis parity gate.

This is a research-only correctness runner.  It performs one canonical V3
self-parity check and one synthetic changed-ranker canary; it never runs a
candidate backtest and never updates configuration, registry, runtime state,
or tracked manifests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import research_v3_equity_roadmap as roadmap  # noqa: E402

from parayabuck.backtest.observation_parity import (  # noqa: E402
    canonical_digest,
    compare_universe_bases,
    strategy_identity,
)
from parayabuck.backtest.product import build_product_universe  # noqa: E402
from parayabuck.backtest.universe import (  # noqa: E402
    PointInTimeUniverse,
    UniverseSnapshot,
    hash_point_in_time_universe,
)
from parayabuck.config import Config, load_config  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.portfolio import portfolio_strategy_from  # noqa: E402
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy  # noqa: E402
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3  # noqa: E402
from parayabuck.universe.observation_set import build_observation_set  # noqa: E402

MANIFEST_PATH = REPO / "data/research_manifests/equity_candidate_observation_parity_2026_08.json"
DEFAULT_OUTPUT = REPO / "data/research/equity_candidate_observation_parity_2026_08/parity_receipt.json"
EXPECTED_UNIVERSE_HASH = "d51ab5c816f9614cf9508d5ab6529e4cd8cad9d7d1feb886b233a3ad3556a011"


def _git_status() -> list[str]:
    output = subprocess.check_output(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=REPO,
        text=True,
    )
    return [line for line in output.splitlines() if line]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        raise SystemExit(f"missing predeclared OCP manifest: {MANIFEST_PATH}")
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("declared_before_results") is not True:
        raise SystemExit("OCP manifest must declare declared_before_results=true")
    if raw.get("research_only") is not True or raw.get("automatic_promotion") is not False:
        raise SystemExit("OCP manifest must remain research-only and non-promoting")
    if raw.get("canonical_control", {}).get("component_hashes", {}).get("universe") != EXPECTED_UNIVERSE_HASH:
        raise SystemExit("OCP manifest canonical universe hash does not match the frozen R1 value")
    return raw


def _assert_strategy_market_timeframe(strategy: PortfolioStrategy, market: Market, timeframe: str) -> None:
    if strategy.market != market:
        raise ValueError("Synthetic parity strategy market mismatch")
    if strategy.timeframe != timeframe:
        raise ValueError("Synthetic parity strategy timeframe mismatch")


class _AlphabeticalCanaryRanker(EquityResidualMomentum1dV3):
    """Unregistered deterministic ranker used only for the synthetic canary."""

    name = "equity_candidate_observation_canary_alphabetical_v1"
    version = "1.0.0"
    display_name = "Candidate observation canary (alphabetical order)"

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        # Include a deliberate out-of-pool symbol to prove the shared builder
        # filters ranker output to scanner-eligible membership.
        return (*tuple(sorted(context.history)), "NOT_ELIGIBLE")


def _synthetic_config(config: Config) -> Config:
    equity_universe = replace(
        config.universe.equity,
        maximum_stream_symbols=3,
        target_shortlist=8,
        reference_symbols=("SPY",),
        inverse_trade_symbols=(),
        fallback_trade_symbols=(),
        scanner=replace(
            config.universe.equity.scanner,
            minimum_price=1.0,
            minimum_median_dollar_volume=1.0,
            minimum_data_completeness=0.80,
            minimum_atr_pct=0.0001,
            maximum_atr_pct=0.20,
            maximum_per_sector=8,
        ),
    )
    return replace(config, universe=replace(config.universe, equity=equity_universe))


def _synthetic_bars(days: int = 360) -> list[Bar]:
    start = datetime(2022, 1, 3, tzinfo=UTC)
    symbols = ("SPY", "AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG")
    bars: list[Bar] = []
    for day in range(days):
        timestamp = start + timedelta(days=day)
        for offset, symbol in enumerate(symbols):
            drift = 0.0001 + offset * 0.00003
            wobble = 1.0 + 0.01 * math.sin(day * 0.73 + offset * 0.41)
            close = 100.0 * (1.0 + drift) ** day * wobble
            bars.append(
                Bar(
                    timestamp=timestamp,
                    symbol=symbol,
                    timeframe="1d",
                    open=close * 0.999,
                    high=close * 1.006,
                    low=close * 0.994,
                    close=close,
                    volume=2_000_000.0 + offset * 10_000.0,
                    market=Market.EQUITY,
                )
            )
    return bars


def _mutate_future_bars(bars: list[Bar], boundary: datetime) -> list[Bar]:
    mutated: list[Bar] = []
    for bar in bars:
        if bar.timestamp <= boundary:
            mutated.append(bar)
            continue
        close = bar.close * 4.0
        mutated.append(
            Bar(
                timestamp=bar.timestamp,
                symbol=bar.symbol,
                timeframe=bar.timeframe,
                open=close * 0.999,
                high=close * 1.006,
                low=close * 0.994,
                close=close,
                volume=bar.volume * 0.01,
                market=bar.market,
            )
        )
    return mutated


def _latest_snapshot(universe: PointInTimeUniverse, effective_at: datetime) -> UniverseSnapshot:
    matches = [snapshot for snapshot in universe.snapshots if snapshot.effective_at == effective_at]
    if len(matches) != 1:
        raise AssertionError(f"expected one snapshot at {effective_at.isoformat()}, got {len(matches)}")
    return matches[0]


def _direct_candidate_observation(
    bars: list[Bar],
    snapshot: UniverseSnapshot,
    strategy: PortfolioStrategy,
) -> tuple[str, ...]:
    history = {
        symbol: tuple(
            sorted(
                (bar for bar in bars if bar.symbol == symbol and bar.timestamp < snapshot.effective_at),
                key=lambda bar: bar.timestamp,
            )
        )
        for symbol in snapshot.symbols
    }
    latest = max((series[-1].timestamp for series in history.values() if series), default=None)
    if latest is None:
        raise AssertionError("synthetic canary has no strictly-prior history")
    direct = build_observation_set(
        strategy,
        history,
        tuple(sorted(snapshot.symbols)),
        market=Market.EQUITY,
        timeframe="1d",
        decision_timestamp=latest,
        maximum_stream_symbols=snapshot.maximum_stream_symbols,
        pinned_symbols=snapshot.pinned_symbols,
    )
    return direct.observation_set


def _canonical_v3_self_parity(config: Config, bars: list[Bar], manifest: dict[str, Any]) -> dict[str, object]:
    # Reuse the exact prepared/deduplicated input boundary used by the clean
    # current-parity control.  The raw v5 CSV contains duplicate provider rows
    # that the historical engine removes before constructing its universe.
    prepared_window = roadmap.prepare_window(
        config,
        bars,
        key="candidate-observation-parity-control",
        start=None,
        end=roadmap.EVAL_END,
    )
    control = prepared_window.universe
    prepared_bars = [*prepared_window.warmup, *prepared_window.prepared[0]]
    explicit_strategy = portfolio_strategy_from(
        "equity_residual_momentum_v3",
        config.strategy.equity.parameters,
        timeframe="1d",
    )
    explicit = build_product_universe(
        config,
        prepared_bars,
        market=Market.EQUITY,
        end=roadmap.EVAL_END,
        timeframe="1d",
        observation_strategy_override=explicit_strategy,
    )
    # The frozen R1 receipt's ``universe`` component is the canonical digest
    # of ``PointInTimeUniverse.to_dict()`` (the same representation used by
    # ``research_v3_current_parity_control.py``), not the runtime
    # ``hash_point_in_time_universe`` deployment version.
    control_hash = canonical_digest(control.to_dict())
    explicit_hash = canonical_digest(explicit.to_dict())
    expected = manifest["canonical_control"]["component_hashes"]
    if control_hash != EXPECTED_UNIVERSE_HASH or explicit_hash != EXPECTED_UNIVERSE_HASH:
        raise SystemExit("canonical V3/default or explicit override universe hash drifted")
    if any(
        snapshot.observation_strategy_name != "equity_residual_momentum_v3"
        or snapshot.observation_strategy_version != "3.0.0"
        for snapshot in explicit.snapshots
    ):
        raise SystemExit("explicit V3 override did not preserve strategy identity")
    comparison = compare_universe_bases(
        control,
        explicit,
        control_strategy=portfolio_strategy_from(
            "equity_residual_momentum_v3",
            config.strategy.equity.parameters,
            timeframe="1d",
        ),
        candidate_strategy=explicit_strategy,
    )
    if comparison["passed"] is not True:
        raise SystemExit(f"explicit V3 self-parity failed: {comparison['failure_reasons']}")
    return {
        "control_universe_hash": control_hash,
        "explicit_override_universe_hash": explicit_hash,
        "expected_universe_hash": expected["universe"],
        "all_snapshots_identity": True,
        "comparison": comparison,
        "pins_and_capacity": {
            "mismatch_count": comparison["mismatch_counts"]["configured_pins"]
            + comparison["mismatch_counts"]["maximum_stream_capacity"]
        },
    }


def _synthetic_changed_ranker_canary(config: Config) -> dict[str, object]:
    synthetic_config = _synthetic_config(config)
    bars = _synthetic_bars()
    params = dict(synthetic_config.strategy.equity.parameters)
    control_strategy = portfolio_strategy_from(
        synthetic_config.strategy.equity.name,
        params,
        timeframe="1d",
    )
    candidate_strategy = _AlphabeticalCanaryRanker(params)
    execution_strategy = _AlphabeticalCanaryRanker(params)
    _assert_strategy_market_timeframe(candidate_strategy, Market.EQUITY, "1d")
    _assert_strategy_market_timeframe(execution_strategy, Market.EQUITY, "1d")
    from parayabuck.backtest.observation_parity import assert_strategy_identity

    assert_strategy_identity(candidate_strategy, execution_strategy)
    control = build_product_universe(
        synthetic_config, bars, market=Market.EQUITY, end=bars[-1].timestamp, timeframe="1d"
    )
    candidate = build_product_universe(
        synthetic_config,
        bars,
        market=Market.EQUITY,
        end=bars[-1].timestamp,
        timeframe="1d",
        observation_strategy_override=candidate_strategy,
    )
    comparison = compare_universe_bases(
        control,
        candidate,
        control_strategy=control_strategy,
        candidate_strategy=candidate_strategy,
    )
    if comparison["passed"] is not True:
        raise SystemExit(f"synthetic scanner/pool parity failed: {comparison['failure_reasons']}")
    if comparison["observation_set"]["sequence_change_count"] <= 0:
        raise SystemExit("synthetic changed-ranker canary did not change observation membership/order")
    latest = candidate.snapshots[-1]
    direct = _direct_candidate_observation(bars, latest, candidate_strategy)
    if latest.observation_set != direct:
        raise SystemExit("candidate observation set differs from direct build_observation_set result")
    probe = candidate.snapshots[-2]
    mutated = _mutate_future_bars(bars, probe.effective_at)
    future_changed = build_product_universe(
        synthetic_config,
        mutated,
        market=Market.EQUITY,
        end=mutated[-1].timestamp,
        timeframe="1d",
        observation_strategy_override=_AlphabeticalCanaryRanker(params),
    )
    if _latest_snapshot(candidate, probe.effective_at) != _latest_snapshot(future_changed, probe.effective_at):
        raise SystemExit("future bars changed the pre-session synthetic observation snapshot")
    repeated = build_product_universe(
        synthetic_config,
        bars,
        market=Market.EQUITY,
        end=bars[-1].timestamp,
        timeframe="1d",
        observation_strategy_override=_AlphabeticalCanaryRanker(params),
    )
    candidate_hash = hash_point_in_time_universe(candidate)
    repeated_hash = hash_point_in_time_universe(repeated)
    if candidate_hash != repeated_hash:
        raise SystemExit("synthetic candidate universe hash is nondeterministic")
    return {
        "data_sha256": canonical_digest(bars),
        "control_strategy": strategy_identity(control_strategy),
        "candidate_strategy": strategy_identity(candidate_strategy),
        "fresh_execution_strategy": strategy_identity(execution_strategy),
        "comparison": comparison,
        "direct_observation_set": list(direct),
        "candidate_latest_observation_set": list(latest.observation_set),
        "future_bar_leak_detected": False,
        "candidate_rank_outside_pool_count": comparison["observation_contract"]["candidate"][
            "observation_outside_pool_count"
        ],
        "candidate_universe_hash": candidate_hash,
        "repeated_candidate_universe_hash": repeated_hash,
    }


def run(output: Path = DEFAULT_OUTPUT) -> dict[str, object]:
    dirty_before = _git_status()
    if dirty_before:
        raise SystemExit(
            "candidate observation parity requires a clean tracked worktree; dirty paths: " + ", ".join(dirty_before)
        )
    manifest = _load_manifest()
    config = load_config(str(REPO / "config/settings.yaml"))
    bars = roadmap.load_bars(roadmap.DATASET_V5)
    e1 = _canonical_v3_self_parity(config, bars, manifest)
    e2 = _synthetic_changed_ranker_canary(config)
    dirty_after = _git_status()
    if dirty_after:
        raise SystemExit("candidate observation parity changed tracked worktree paths: " + ", ".join(dirty_after))
    source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    payload: dict[str, object] = {
        "schema_version": "1.0.0",
        "receipt_name": "EQUITY_CANDIDATE_OBSERVATION_PARITY_OCP1",
        "source_commit": source_commit,
        "base_commit": manifest["base_commit"],
        "python_version": platform.python_version(),
        "runner_sha256": _sha256(Path(__file__)),
        "config_digest_sha256": roadmap.config_digest(config),
        "dataset": {
            "bars_sha256": _sha256(roadmap.DATASET_V5),
            "lifetimes_sha256": _sha256(roadmap.LIFETIMES_V5),
            "corporate_actions_sha256": _sha256(roadmap.CA_V5),
        },
        "policy": {
            "research_only": True,
            "runtime_reads_artifacts": False,
            "automatic_promotion": False,
            "candidate_economics_run": False,
        },
        "checks": {"E1_canonical_v3_explicit_self_parity": e1, "E2_synthetic_changed_ranker_canary": e2},
        "dirty_paths_before": dirty_before,
        "dirty_paths_after": dirty_after,
    }
    payload["receipt_sha256"] = canonical_digest(payload)
    output = output if output.is_absolute() else REPO / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    payload["raw_receipt_sha256"] = _sha256(output)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = run(args.output)
    print(json.dumps(payload, indent=2, sort_keys=True))
    print(f"raw_receipt_sha256={payload['raw_receipt_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
