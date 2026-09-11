from __future__ import annotations

import copy
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

import scripts.research.build_r46_native_ledger_attestation_2026_09_10 as native_builder
import scripts.research.r46_treasury_auction_unwind_tlt_v2_2026_09_10 as r46_runner
import scripts.research.r46_v2_preflight_2026_09_10 as preflight
import scripts.research.verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10 as r46_verifier
from parayabuck.backtest.portfolio_engine import OrderSide, PortfolioOrder
from parayabuck.backtest.terminal_aware_engine import TerminalAwareResearchEngine
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from scripts.research.build_r46_native_ledger_attestation_2026_09_10 import (
    build_attestation,
    write_attestation,
)
from scripts.research.r46_treasury_auction_unwind_tlt_v2_2026_09_10 import (
    COST_MULTIPLIERS,
    ELIGIBILITY,
    POLICY,
    PRE_RUN_OK,
    R46EventSchedule,
    R46IncumbentInputs,
    R46OverlayStrategy,
    R46PackageInputs,
    SharedAccountLedger,
    _build_statistics,
    _economic_ledger_payload,
    _R46IncumbentStrategy,
    _run_r46_engine,
    _run_timing_placebo_cell,
    _shift_r46_package_for_placebo,
    _validate_v5_incumbent_dependencies,
    build_native_smoke_dataset,
    build_r46_market_dataset,
    consume_one_shot,
    native_adapter_smoke,
    reserve_one_shot,
    run_native_adapter,
    run_synthetic,
    schedule_r46_holding,
)
from scripts.research.verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10 import (
    _verify_economic_structural,
    verify_attestation,
    verify_payload,
)

TS = datetime(2025, 1, 15, 21, 0, tzinfo=UTC)
TS2 = datetime(2025, 1, 16, 21, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[2]


def _canonical(value: Mapping[str, Any], field: str) -> str:
    body = copy.deepcopy(value)
    integrity = body.get("integrity")
    assert isinstance(integrity, dict)
    integrity.pop(field, None)
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _write_json(path: Path, value: dict[str, object]) -> str:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pinned_gate(tmp_path: Path) -> Path:
    manifest: dict[str, object] = {
        "schema_version": "r46-governance-manifest-v2",
        "research_id": "r46_treasury_auction_unwind_tlt_v2_2026_09_10",
        "candidate_id": "R46",
        "integrity": {"manifest_sha256": ""},
    }
    manifest["integrity"]["manifest_sha256"] = _canonical(manifest, "manifest_sha256")  # type: ignore[index]
    package: dict[str, object] = {
        "schema": "r46-treasury-auction-unwind-data-package-v2",
        "research_id": manifest["research_id"],
        "candidate_id": "R46",
        "integrity": {"manifest_sha256": ""},
    }
    package["integrity"]["manifest_sha256"] = _canonical(package, "manifest_sha256")  # type: ignore[index]
    manifest_bytes = _write_json(tmp_path / "manifest.json", manifest)
    package_bytes = _write_json(tmp_path / "package.json", package)
    gate: dict[str, object] = {
        "schema_version": "r46-v2-preflight-receipt-v2",
        "status": PRE_RUN_OK,
        "research_id": manifest["research_id"],
        "candidate_id": "R46",
        "manifest_path": "manifest.json",
        "manifest_sha256": manifest["integrity"]["manifest_sha256"],  # type: ignore[index]
        "manifest_file_sha256": manifest_bytes,
        "package_path": "package.json",
        "package_sha256": package["integrity"]["manifest_sha256"],  # type: ignore[index]
        "package_file_sha256": package_bytes,
        "research_only": True,
        "read_only": True,
        "economic_execution": False,
        "economic_run": False,
        "economic_outcomes_accessed": False,
        "outcome_artifacts_present": False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "integration_candidate": False,
        "promotion": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
        "integrity": {"receipt_sha256": ""},
    }
    gate["integrity"]["receipt_sha256"] = _canonical(gate, "receipt_sha256")  # type: ignore[index]
    _write_json(tmp_path / "preflight.json", gate)
    return tmp_path / "preflight.json"


def _fill(
    symbol: str,
    side: str,
    qty: float,
    price: float,
    owner: str,
    *,
    decision_timestamp: datetime = TS,
    fee: float = 0.0,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "fee": fee,
        "owner": owner,
        "decision_timestamp": decision_timestamp.isoformat(),
        "client_order_id": f"{owner}-{symbol}-{side.lower()}",
    }


def _native_event_fixture(tmp_path: Path) -> tuple[R46PackageInputs, R46IncumbentInputs]:
    """Small package-shaped fixture that still traverses the real engine."""

    start = datetime(2024, 1, 1, 14, 30, tzinfo=UTC)
    incumbent_bars: list[Bar] = []
    tlt_bars: list[Bar] = []
    for index in range(330):
        timestamp = start + timedelta(days=index)
        for symbol, price in (
            ("SPY", 100.0 + index * 0.01),
            ("AAA", 90.0 + index * 0.03),
            ("BBB", 80.0 + index * 0.02),
        ):
            incumbent_bars.append(Bar(timestamp, symbol, "1d", price, price, price, price, 1_000_000.0, Market.EQUITY))
        price = 100.0 + index * 0.01
        # Keep the synthetic fixture's mandated 2.5% sleeve fully fillable at
        # the exact open; production participation/catch-up semantics remain
        # exercised by the dedicated partial-fill tests below.
        tlt_bars.append(Bar(timestamp, "TLT", "1d", price, price, price, price, 10_000_000.0, Market.EQUITY))

    auction_date = (start + timedelta(days=300)).date().isoformat()
    entry_open = start + timedelta(days=301)
    future_opens = tuple(start + timedelta(days=301 + offset) for offset in range(6))
    event_available = start + timedelta(days=300, hours=1)
    schedule = R46EventSchedule(
        event_id="fixture-event-1",
        security_identifier="TLT-TEST",
        auction_session_date=auction_date,
        available_at=event_available,
        entry_session_date=entry_open.date().isoformat(),
        entry_open=entry_open,
        exit_session_date=future_opens[5].date().isoformat(),
        exit_open=future_opens[5],
        entry_decision=event_available,
        exit_decision=future_opens[4],
        holding_intervals=5,
        future_open_timestamps=future_opens,
    )
    package = R46PackageInputs(
        package_path=tmp_path / "fixture-package.json",
        root=tmp_path,
        package={"integrity": {"manifest_sha256": "a" * 64}},
        documents=({"canonical_event_id": schedule.event_id},),
        events=({"canonical_event_id": schedule.event_id},),
        tlt_bars=tuple(tlt_bars),
        tlt_dividends=(
            CorporateAction(
                start + timedelta(days=303),
                "TLT",
                CorporateActionType.DIVIDEND,
                1.0,
            ),
        ),
        eligibility=(),
        calendar=tuple(
            {
                "regular_session_date": (start + timedelta(days=index)).date().isoformat(),
                "regular_open_timestamp": (start + timedelta(days=index)).isoformat(),
                "holiday_or_closure_reason": "NONE",
            }
            for index in range(330)
        ),
        schedules=(schedule,),
        markers=(
            Bar(
                event_available,
                "TLT",
                "1d",
                130.0,
                130.0,
                130.0,
                130.0,
                1_000_000.0,
                Market.EQUITY,
            ),
        ),
        marker_sources={schedule.event_id: "TEST_ONLY_FIXTURE_MARKER"},
    )
    incumbent = R46IncumbentInputs(tuple(incumbent_bars), None, ())
    return package, incumbent


def _valid_native_attestation_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, object], Path]:
    def write_artifact(path: Path, schema: str) -> None:
        payload: dict[str, object] = {"schema": schema, "integrity": {"manifest_sha256": ""}}
        payload["integrity"]["manifest_sha256"] = _canonical(payload, "manifest_sha256")  # type: ignore[index]
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    governance = tmp_path / "governance.json"
    package = tmp_path / "package.json"
    write_artifact(governance, "r46-governance-manifest-v2")
    write_artifact(package, "r46-treasury-auction-unwind-data-package-v2")
    runner_path = tmp_path / "runner.py"
    verifier_path = tmp_path / "verifier.py"
    source_path = tmp_path / "portfolio_engine.py"
    runner_path.write_text("runner-v1\n", encoding="utf-8")
    verifier_path.write_text("verifier-v1\n", encoding="utf-8")
    source_path.write_text("engine-v1\n", encoding="utf-8")
    monkeypatch.setattr(native_builder, "REPO", tmp_path)
    monkeypatch.setattr(native_builder, "DEFAULT_GOVERNANCE", governance)
    monkeypatch.setattr(native_builder, "DEFAULT_PACKAGE", package)
    monkeypatch.setattr(native_builder, "RUNNER", "runner.py")
    monkeypatch.setattr(native_builder, "VERIFIER", "verifier.py")
    monkeypatch.setattr(native_builder, "SOURCE", "portfolio_engine.py")
    return native_builder.build_attestation(), runner_path


def test_runner_refuses_mapping_and_requires_pinned_on_disk_gate(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="BLOCKED_V2_PRE_RUN_GATE"):
        run_synthetic(gate={"status": PRE_RUN_OK}, decisions=(), fills=())
    gate = _pinned_gate(tmp_path)
    result = run_synthetic(gate=gate, decisions=(), fills=())
    assert result["status"] == "TEST_ONLY_SYNTHETIC_LEDGER"
    gate_payload = json.loads(gate.read_text(encoding="utf-8"))
    gate_payload["manifest_sha256"] = "0" * 64
    _write_json(gate, gate_payload)
    with pytest.raises(RuntimeError, match="BLOCKED_V2_PRE_RUN_GATE"):
        run_synthetic(gate=gate, decisions=(), fills=())


def test_persisted_preflight_forwards_all_explicit_evidence_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The economic runner must recompute the same external evidence as preflight."""

    source_commit = "c" * 40
    freeze_commit = "f" * 40
    receipt: dict[str, object] = {
        "source_commit": source_commit,
        "freeze_commit": freeze_commit,
        "pit_source_attestation_path": "evidence/pit.json",
        "native_attestation_path": "evidence/native.json",
        "independent_software_review_path": "evidence/review.json",
    }
    observed: dict[str, object] = {}

    def fake_verify(manifest_path: Path, **kwargs: object) -> dict[str, object]:
        observed.update(kwargs)
        return {
            "status": PRE_RUN_OK,
            "preflight_receipt": receipt,
            "source_commit": source_commit,
            "freeze_commit": freeze_commit,
        }

    monkeypatch.setattr(preflight, "verify_v2_manifest", fake_verify)
    result = r46_runner._verify_persisted_preflight(
        manifest_path=tmp_path / "governance.json",
        package_path=tmp_path / "package.json",
        receipt=receipt,
        repo_root=tmp_path,
        preflight_receipt_path=tmp_path / "gate.json",
    )

    assert result["status"] == PRE_RUN_OK
    assert observed["pit_source_attestation_path"] == Path("evidence/pit.json")
    assert observed["native_ledger_attestation_path"] == Path("evidence/native.json")
    assert observed["independent_software_review_path"] == Path("evidence/review.json")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("incumbent_inputs", object()),
        ("config", object()),
        ("starting_equity", 123_456.0),
        ("cost_multipliers", (1.0, 2.0)),
        ("allow_marker_fallback", True),
    ),
)
def test_production_package_runner_rejects_caller_economic_overrides(tmp_path: Path, field: str, value: object) -> None:
    kwargs: dict[str, object] = {"gate": tmp_path / "gate.json", "output_dir": tmp_path / "out"}
    kwargs[field] = value
    with pytest.raises(RuntimeError, match="BLOCKED_V2_PRODUCTION_OVERRIDE"):
        r46_runner.run_package_backed(**kwargs)


def test_production_config_failure_blocks_and_static_universe_is_test_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from parayabuck import config as config_module

    def fail_load(_path: str) -> object:
        raise OSError("missing settings")

    monkeypatch.setattr(config_module, "load_config", fail_load)
    with pytest.raises(RuntimeError, match="BLOCKED_CONFIG_SOURCE"):
        r46_runner._load_r46_config(None, repo_root=tmp_path)
    assert r46_runner._load_r46_config(None, repo_root=tmp_path, test_only=True) is None

    package, incumbent = _native_event_fixture(tmp_path)
    with pytest.raises(RuntimeError, match="BLOCKED_CONFIG_SOURCE"):
        r46_runner._build_r46_universe(incumbent, config=None)
    universe, version, status = r46_runner._build_r46_universe(incumbent, config=None, test_only=True)
    assert universe is None
    assert version == "r46-static-fixture"
    assert status == "STATIC_TEST_CONTEXT"


def test_production_pit_loader_is_repo_root_relative_and_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="BLOCKED_PIT_SOURCE:seam_root_mismatch"):
        r46_runner.load_incumbent_pit_inputs(repo_root=tmp_path)


def test_event_marker_reuses_cached_incumbent_target_without_v3_rerun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_timestamp = datetime(2025, 1, 15, 14, 0, tzinfo=UTC)
    schedule = R46EventSchedule(
        "cached-base-event",
        "TLT-TEST",
        "2025-01-15",
        event_timestamp,
        "2025-01-16",
        datetime(2025, 1, 16, 14, 30, tzinfo=UTC),
        "2025-01-21",
        datetime(2025, 1, 21, 14, 30, tzinfo=UTC),
        event_timestamp,
        datetime(2025, 1, 20, 14, 30, tzinfo=UTC),
        5,
        tuple(datetime(2025, 1, 16 + index, 14, 30, tzinfo=UTC) for index in range(6)),
    )
    calls: list[datetime] = []
    base_target = TargetPortfolio(
        strategy_name="equity_residual_momentum_v3",
        strategy_version="3.0.0",
        decision_timestamp=event_timestamp - timedelta(days=7),
        weights=(TargetWeight("AAA", 0.05),),
        cash_weight=0.95,
    )

    def fake_v3(self: object, context: PortfolioContext) -> TargetPortfolio:
        calls.append(context.decision_timestamp)
        return base_target

    monkeypatch.setattr(r46_runner.EquityResidualMomentum1dV3, "decide", fake_v3)
    strategy = R46OverlayStrategy((schedule,))

    def context(timestamp: datetime) -> PortfolioContext:
        return PortfolioContext(
            decision_timestamp=timestamp,
            market=Market.EQUITY,
            timeframe="1d",
            history={},
            universe=None,
            universe_version="fixture",
            equity=100_000.0,
            holdings={},
            prices={},
            benchmark_symbol="SPY",
        )

    weekly_target = strategy.decide(context(event_timestamp - timedelta(days=7)))
    event_target = strategy.decide(context(event_timestamp))

    assert calls == [event_timestamp - timedelta(days=7)]
    assert tuple(weight.symbol for weight in event_target.weights if weight.symbol != "TLT") == tuple(
        weight.symbol for weight in weekly_target.weights
    )
    assert any(weight.symbol == "TLT" for weight in event_target.weights)


def test_event_boundary_preserves_existing_incumbent_pending_orders(tmp_path: Path) -> None:
    package, _incumbent = _native_event_fixture(tmp_path)
    schedule = package.schedules[0]
    strategy = R46OverlayStrategy((schedule,))
    engine = r46_runner._R46PortfolioBacktestEngine(
        strategy,
        r46_runner.R46EventRebalancePolicy(Market.EQUITY, "weekly", frozenset({schedule.entry_decision})),
        starting_equity=100_000.0,
        max_participation_pct=1.0,
        liquidity_lookback_bars=1,
        turnover_band=0.0,
        minimum_order_notional=0.0,
        allow_static_universe=True,
        event_boundaries=(schedule.entry_decision,),
    )
    engine._cash = 100_000.0
    engine._last_price = {"AAA": 100.0, "TLT": 100.0}
    incumbent_order = PortfolioOrder(
        "incumbent-pending",
        "AAA",
        Market.EQUITY,
        OrderSide.BUY,
        10.0,
        schedule.entry_decision - timedelta(days=1),
        schedule.entry_decision - timedelta(days=1),
    )
    engine._pending = [incumbent_order]
    target = TargetPortfolio(
        strategy_name="equity_residual_momentum_v3",
        strategy_version="3.0.0",
        decision_timestamp=schedule.entry_decision,
        weights=(TargetWeight("AAA", 0.05), TargetWeight("TLT", 0.10)),
        cash_weight=0.85,
    )

    engine._apply_target(target)

    assert incumbent_order in engine._pending
    assert [order.symbol for order in engine._pending].count("AAA") == 1
    assert [order.symbol for order in engine._pending].count("TLT") == 1


def test_candidate_control_incumbent_engine_path_is_exact_except_tlt(tmp_path: Path) -> None:
    package, incumbent = _native_event_fixture(tmp_path)
    dataset = build_r46_market_dataset(package, incumbent)
    boundaries = tuple(
        sorted(
            {
                timestamp
                for schedule in package.schedules
                for timestamp in (schedule.entry_decision, schedule.exit_decision)
            }
        )
    )
    candidate = _run_r46_engine(
        dataset,
        R46OverlayStrategy(package.schedules),
        cost_multiplier=1.0,
        starting_equity=100_000.0,
        config=None,
        universe=None,
        universe_version="TEST_ONLY_FIXTURE",
        event_boundaries=boundaries,
        marker_timestamps=tuple(marker.timestamp for marker in package.markers),
    )
    control = _run_r46_engine(
        dataset,
        r46_runner._R46IncumbentStrategy(dict(r46_runner.FROZEN_V3_PARAMETERS)),
        cost_multiplier=1.0,
        starting_equity=100_000.0,
        config=None,
        universe=None,
        universe_version="TEST_ONLY_FIXTURE",
        marker_timestamps=tuple(marker.timestamp for marker in package.markers),
    )

    candidate_rebalances = tuple(item for item in candidate.rebalances if item.decision_timestamp not in boundaries)
    control_rebalances = tuple(control.rebalances)
    assert candidate_rebalances == control_rebalances
    assert tuple(fill for fill in candidate.fills if fill.symbol != "TLT") == tuple(
        fill for fill in control.fills if fill.symbol != "TLT"
    )
    assert tuple((fill.side, fill.timestamp) for fill in candidate.fills if fill.symbol == "TLT") == (
        (OrderSide.BUY, package.schedules[0].entry_open),
        (OrderSide.SELL, package.schedules[0].exit_open),
    )
    candidate_weights = tuple(
        (timestamp, {symbol: weight for symbol, weight in weights.items() if symbol != "TLT"})
        for timestamp, weights in candidate.weight_history
        if timestamp not in boundaries
    )
    control_weights = tuple(
        (timestamp, {symbol: weight for symbol, weight in weights.items() if symbol != "TLT"})
        for timestamp, weights in control.weight_history
    )
    assert candidate_weights == control_weights


def test_r46_control_matches_canonical_terminal_aware_v5_lifecycle_for_non_tlt(tmp_path: Path) -> None:
    """The R46 control must use the same lifecycle seam as the V5 incumbent."""

    package, incumbent = _native_event_fixture(tmp_path)
    dataset = build_r46_market_dataset(package, incumbent)
    strategy = r46_runner._R46IncumbentStrategy(dict(r46_runner.FROZEN_V3_PARAMETERS))
    control = _run_r46_engine(
        dataset,
        strategy,
        cost_multiplier=1.0,
        starting_equity=100_000.0,
        config=None,
        universe=None,
        universe_version="TEST_ONLY_FIXTURE",
        marker_timestamps=tuple(marker.timestamp for marker in package.markers),
    )
    canonical = TerminalAwareResearchEngine(
        strategy=r46_runner._R46IncumbentStrategy(dict(r46_runner.FROZEN_V3_PARAMETERS)),
        rebalance_policy=r46_runner.rebalance_policy_for(strategy),
        terminal_map=dict(getattr(incumbent, "terminal_map", {})),
        policy="T1",
        starting_equity=100_000.0,
        slippage_bps=2.0,
        spread_bps=0.0,
        commission=0.0,
        crypto_taker_fee_bps=0.0,
        equity_taf_per_share=0.0,
        equity_taf_cap=0.0,
        equity_cat_per_share=0.0,
        equity_sec_fee_rate=0.0,
        equity_use_effective_dated_fees=False,
        benchmark_symbol="SPY",
        max_participation_pct=0.10,
        liquidity_lookback_bars=20,
        turnover_band=0.0,
        minimum_order_notional=0.0,
        universe=None,
        universe_version="TEST_ONLY_FIXTURE",
        allow_static_universe=True,
    ).run_dataset(dataset, liquidate_at_end=True)

    control_fills = tuple(fill for fill in control.fills if fill.symbol != "TLT")
    canonical_fills = tuple(fill for fill in canonical.fills if fill.symbol != "TLT")
    assert control_fills == canonical_fills
    assert control.rebalances == canonical.rebalances
    assert control.equity_curve == canonical.equity_curve


def test_r46_effective_dated_equity_fee_stress_scales_official_trade_date_fee(tmp_path: Path) -> None:
    """The production effective-dated regulatory fee path participates in 2x/3x stress."""

    package, incumbent = _native_event_fixture(tmp_path)
    dataset = build_r46_market_dataset(package, incumbent)
    config = r46_runner._load_r46_config(None, repo_root=REPO)
    costs_1x = r46_runner._backtest_costs(config, 1.0)
    costs_2x = r46_runner._backtest_costs(config, 2.0)
    result_1x = _run_r46_engine(
        dataset,
        r46_runner._R46IncumbentStrategy(dict(r46_runner.FROZEN_V3_PARAMETERS)),
        cost_multiplier=1.0,
        starting_equity=100_000.0,
        config=config,
        universe=None,
        universe_version="TEST_ONLY_FIXTURE",
        marker_timestamps=tuple(marker.timestamp for marker in package.markers),
        terminal_map=incumbent.terminal_map,
    )
    result_2x = _run_r46_engine(
        dataset,
        r46_runner._R46IncumbentStrategy(dict(r46_runner.FROZEN_V3_PARAMETERS)),
        cost_multiplier=2.0,
        starting_equity=100_000.0,
        config=config,
        universe=None,
        universe_version="TEST_ONLY_FIXTURE",
        marker_timestamps=tuple(marker.timestamp for marker in package.markers),
        terminal_map=incumbent.terminal_map,
    )
    fee_1x = sum(fill.fee for fill in result_1x.fills)
    fee_2x = sum(fill.fee for fill in result_2x.fills)

    assert costs_1x["equity_use_effective_dated_fees"] is True
    assert costs_2x["equity_use_effective_dated_fees"] is True
    # Effective-dated rates are looked up by trade date; only the explicit
    # multiplier scales them.  The configured flat-rate fields stay as the
    # audit reference and must not masquerade as scaled official rates.
    for field in ("equity_taf_per_share", "equity_taf_cap", "equity_cat_per_share", "equity_sec_fee_rate"):
        assert costs_2x[field] == pytest.approx(getattr(config.backtest, field))
    assert fee_2x == pytest.approx(2.0 * fee_1x)


def test_marker_volume_is_excluded_from_r46_point_in_time_liquidity_history() -> None:
    event_timestamp = datetime(2025, 1, 15, 14, 0, tzinfo=UTC)
    schedule = R46EventSchedule(
        "marker-volume-event",
        "TLT-TEST",
        "2025-01-15",
        event_timestamp,
        "2025-01-16",
        datetime(2025, 1, 16, 14, 30, tzinfo=UTC),
        "2025-01-21",
        datetime(2025, 1, 21, 14, 30, tzinfo=UTC),
        event_timestamp,
        datetime(2025, 1, 20, 14, 30, tzinfo=UTC),
        5,
        tuple(datetime(2025, 1, 16 + index, 14, 30, tzinfo=UTC) for index in range(6)),
    )
    strategy = R46OverlayStrategy((schedule,))
    engine = r46_runner._R46PortfolioBacktestEngine(
        strategy,
        r46_runner.R46EventRebalancePolicy(Market.EQUITY, "weekly", frozenset()),
        liquidity_lookback_bars=2,
        marker_timestamps=(event_timestamp,),
    )
    engine._execution_history[("TLT", "1d")] = [
        Bar(event_timestamp - timedelta(days=1), "TLT", "1d", 100.0, 100.0, 100.0, 100.0, 100.0, Market.EQUITY),
        Bar(event_timestamp, "TLT", "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000.0, Market.EQUITY),
    ]
    order = PortfolioOrder(
        "r46-marker-test",
        "TLT",
        Market.EQUITY,
        OrderSide.BUY,
        1.0,
        event_timestamp + timedelta(days=1),
        event_timestamp + timedelta(days=1),
    )

    assert engine._known_average_volume(order, "1d") == 100.0


def test_tlt_remainder_after_exact_open_is_not_carried_to_a_later_bar() -> None:
    entry_decision = datetime(2025, 1, 15, 14, 0, tzinfo=UTC)
    entry_open = datetime(2025, 1, 16, 14, 30, tzinfo=UTC)
    schedule = R46EventSchedule(
        "no-catch-up-event",
        "TLT-TEST",
        "2025-01-15",
        entry_decision,
        "2025-01-16",
        entry_open,
        "2025-01-21",
        datetime(2025, 1, 21, 14, 30, tzinfo=UTC),
        entry_decision,
        datetime(2025, 1, 20, 14, 30, tzinfo=UTC),
        5,
        tuple(datetime(2025, 1, 16 + index, 14, 30, tzinfo=UTC) for index in range(6)),
    )
    strategy = R46OverlayStrategy((schedule,))
    engine = r46_runner._R46PortfolioBacktestEngine(
        strategy,
        r46_runner.R46EventRebalancePolicy(Market.EQUITY, "weekly", frozenset()),
        liquidity_lookback_bars=1,
    )
    order = PortfolioOrder(
        "r46-no-catch-up",
        "TLT",
        Market.EQUITY,
        OrderSide.BUY,
        10.0,
        entry_decision,
        entry_decision,
    )
    engine._pending = [order]
    late_bar = Bar(
        entry_open + timedelta(days=1),
        "TLT",
        "1d",
        100.0,
        100.0,
        100.0,
        100.0,
        1_000_000.0,
        Market.EQUITY,
    )
    engine._execution_history[("TLT", "1d")] = [
        Bar(entry_decision - timedelta(days=1), "TLT", "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000.0, Market.EQUITY)
    ]

    engine._fill_side(late_bar, OrderSide.BUY)

    assert engine._fills == []
    assert engine._pending == []


def test_tlt_pending_order_waits_for_exact_open_after_intermediate_bars() -> None:
    entry_decision = datetime(2025, 1, 15, 14, 0, tzinfo=UTC)
    entry_open = datetime(2025, 1, 16, 14, 30, tzinfo=UTC)
    schedule = R46EventSchedule(
        "exact-open-event",
        "TLT-TEST",
        "2025-01-15",
        entry_decision,
        "2025-01-16",
        entry_open,
        "2025-01-21",
        datetime(2025, 1, 21, 14, 30, tzinfo=UTC),
        entry_decision,
        datetime(2025, 1, 20, 14, 30, tzinfo=UTC),
        5,
        tuple(datetime(2025, 1, 16 + index, 14, 30, tzinfo=UTC) for index in range(6)),
    )
    strategy = R46OverlayStrategy((schedule,))
    engine = r46_runner._R46PortfolioBacktestEngine(
        strategy,
        r46_runner.R46EventRebalancePolicy(Market.EQUITY, "weekly", frozenset()),
        liquidity_lookback_bars=1,
    )
    order = PortfolioOrder(
        "r46-exact-open",
        "TLT",
        Market.EQUITY,
        OrderSide.BUY,
        10.0,
        entry_decision,
        entry_decision,
    )
    engine._pending = [order]
    engine._execution_history[("TLT", "1d")] = [
        Bar(entry_decision - timedelta(days=1), "TLT", "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000.0, Market.EQUITY)
    ]

    for timestamp in (entry_open - timedelta(hours=12), entry_open - timedelta(hours=1)):
        engine._fill_side(
            Bar(timestamp, "TLT", "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000.0, Market.EQUITY),
            OrderSide.BUY,
        )
        assert engine._fills == []
        assert engine._pending == [order]

    engine._fill_side(
        Bar(entry_open, "TLT", "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000.0, Market.EQUITY),
        OrderSide.BUY,
    )

    assert len(engine._fills) == 1
    assert engine._fills[0].timestamp == entry_open
    assert engine._pending == []


def test_partial_tlt_entry_or_exit_is_not_an_eligible_event_episode(tmp_path: Path) -> None:
    package, _incumbent = _native_event_fixture(tmp_path)
    schedule = package.schedules[0]
    ledger: dict[str, object] = {
        "event_records": [{"event_id": schedule.event_id, "status": "ACCEPTED"}],
        "event_fill_statuses": [
            {
                "event_id": schedule.event_id,
                "entry_status": "PARTIAL",
                "exit_status": "FULL",
            }
        ],
        "fills": [
            {
                "symbol": "TLT",
                "side": "BUY",
                "qty": 1.0,
                "price": 100.0,
                "fee": 1.0,
                "timestamp": schedule.entry_open.isoformat(),
            },
            {
                "symbol": "TLT",
                "side": "SELL",
                "qty": 1.0,
                "price": 101.0,
                "fee": 1.0,
                "timestamp": schedule.exit_open.isoformat(),
            },
        ],
        "dividend_events": [],
    }

    assert r46_runner._event_episode_deltas(package, ledger, cost_multiplier=1.0) == []


def test_holm_is_limited_to_frozen_inferential_ids_and_marks_diagnostics_not_tested(
    tmp_path: Path,
) -> None:
    package, _incumbent = _native_event_fixture(tmp_path)
    empty_ledger: dict[str, object] = {"event_records": [], "fills": [], "dividend_events": []}
    cells = [
        {
            "cost_multiplier": multiplier,
            "candidate_ledger": empty_ledger,
            "candidate_metrics": {"total_return": 0.0},
            "control_metrics": {"total_return": 0.0},
        }
        for multiplier in COST_MULTIPLIERS
    ]

    statistics = _build_statistics(package, cells)

    holm = statistics["holm"]
    assert r46_runner.INFERENTIAL_STATISTIC_IDS == (
        r46_runner.STATISTIC_IDS[0],
        r46_runner.STATISTIC_IDS[1],
        r46_runner.STATISTIC_IDS[3],
        r46_runner.STATISTIC_IDS[4],
    )
    assert holm["family_size"] == len(r46_runner.INFERENTIAL_STATISTIC_IDS)
    assert tuple(holm["ids"]) == r46_runner.INFERENTIAL_STATISTIC_IDS
    assert set(holm["adjusted_p_values"]) == set(r46_runner.INFERENTIAL_STATISTIC_IDS)
    assert statistics["unchanged_incumbent_comparison"]["not_tested"] is True
    assert statistics["unchanged_incumbent_comparison"]["p_value"] is None
    assert statistics["capacity"]["not_tested"] is True
    assert statistics["capacity"]["p_value"] is None
    assert statistics["missingness"]["not_tested"] is True
    assert statistics["missingness"]["p_value"] is None
    assert statistics["timing_placebo_plus_10_sessions"]["status"] == "BLOCKED_TIMING_PLACEBO_ENGINE_CELL"
    assert statistics["timing_placebo_plus_10_sessions"]["not_tested"] is False


def test_timing_placebo_runs_shifted_candidate_and_control_native_engine_cell(tmp_path: Path) -> None:
    package, incumbent = _native_event_fixture(tmp_path)

    shifted = _shift_r46_package_for_placebo(package)
    assert shifted.schedules[0].entry_open > package.schedules[0].entry_open
    assert shifted.schedules[0].exit_open > package.schedules[0].exit_open
    assert shifted.markers[0].timestamp > package.markers[0].timestamp

    placebo = _run_timing_placebo_cell(
        package,
        incumbent,
        config=None,
        universe=None,
        universe_version="TEST_ONLY_FIXTURE",
        manifest_sha256="a" * 64,
        gate_sha256="b" * 64,
    )

    assert placebo["status"] in {
        "REPORTED_NATIVE_ENGINE_DIAGNOSTIC_ONLY",
        "BLOCKED_TIMING_PLACEBO_NO_COMPLETE_EPISODES",
    }
    assert placebo["native_engine"] == "TerminalAwareResearchEngine"
    assert placebo["engine_invocations"] == 2
    assert placebo["shift_sessions"] == 10
    assert placebo["not_tested"] is False
    assert "candidate_metrics" in placebo
    assert "control_metrics" in placebo
    assert "candidate_minus_control" in placebo
    assert "event_episode_deltas" in placebo
    assert "p_value" in placebo


def test_timing_placebo_persists_rehashed_economic_ledgers_with_shifted_identity(
    tmp_path: Path,
) -> None:
    package, incumbent = _native_event_fixture(tmp_path)

    placebo = _run_timing_placebo_cell(
        package,
        incumbent,
        config=None,
        universe=None,
        universe_version="TEST_ONLY_FIXTURE",
        manifest_sha256="a" * 64,
        gate_sha256="b" * 64,
    )

    assert placebo["status"] in {
        "REPORTED_NATIVE_ENGINE_DIAGNOSTIC_ONLY",
        "BLOCKED_TIMING_PLACEBO_NO_COMPLETE_EPISODES",
    }
    shifted = _shift_r46_package_for_placebo(package)
    expected_schedules = [schedule.to_dict() for schedule in shifted.schedules]
    expected_markers = [
        {
            "timestamp": marker.timestamp.isoformat(),
            "symbol": marker.symbol,
            "source": shifted.marker_sources.get(package.schedules[0].event_id),
        }
        for marker in shifted.markers
    ]
    for arm, control in (("candidate", False), ("control", True)):
        ledger = cast(dict[str, Any], placebo[f"{arm}_ledger"])
        assert ledger["status"] == "R46_V2_ECONOMIC_RUN"
        assert ledger["native_engine"] == "TerminalAwareResearchEngine"
        assert ledger["economic_execution"] is True
        assert ledger["economic_run"] is True
        assert ledger["outcome_artifacts_present"] is True
        assert ledger["arm"] == arm
        assert ledger["control"] is control
        assert ledger["cost_multiplier"] == 1.0
        assert ledger["manifest_canonical_sha256"] == "a" * 64
        assert ledger["preflight_receipt_sha256"] == "b" * 64
        assert ledger["package_canonical_sha256"] == package.package_canonical_sha256
        assert ledger["event_schedules"] == expected_schedules
        assert ledger["marker_records"] == expected_markers
        assert "decisions" in ledger
        assert "rebalances" in ledger
        assert ledger["ledger_sha256"] == r46_runner._canonical_hash(ledger, "ledger_sha256")
        assert _verify_economic_structural(ledger)["status"] == "VERIFIED"


def test_incumbent_comparison_uses_complete_primary_event_endpoints_and_sign_flip(
    tmp_path: Path,
) -> None:
    package, _incumbent = _native_event_fixture(tmp_path)
    schedule = package.schedules[0]

    def cell_ledger(multiplier: float, terminal_equity: float) -> dict[str, object]:
        return {
            "cost_multiplier": multiplier,
            "event_records": [{"event_id": schedule.event_id, "status": "ACCEPTED"}],
            "event_fill_statuses": [
                {
                    "event_id": schedule.event_id,
                    "entry_status": "FULL",
                    "exit_status": "FULL",
                    "eligible_episode": True,
                }
            ],
            "fills": [
                {
                    "symbol": "TLT",
                    "side": "BUY",
                    "qty": 1.0,
                    "price": 100.0,
                    "fee": 0.0,
                    "timestamp": schedule.entry_open.isoformat(),
                },
                {
                    "symbol": "TLT",
                    "side": "SELL",
                    "qty": 1.0,
                    "price": terminal_equity - 100_000.0 + 100.0,
                    "fee": 0.0,
                    "timestamp": schedule.exit_open.isoformat(),
                },
            ],
            "dividend_events": [],
            "equity_curve_timestamps": [
                schedule.entry_decision.isoformat(),
                schedule.entry_open.isoformat(),
                schedule.exit_decision.isoformat(),
                schedule.exit_open.isoformat(),
            ],
            "equity_curve": [100_000.0, 100_000.0, 100_000.0, terminal_equity],
        }

    cells = [
        {
            "cost_multiplier": multiplier,
            "candidate_ledger": cell_ledger(multiplier, 100_010.0),
            "control_ledger": cell_ledger(multiplier, 100_005.0),
            "candidate_metrics": {"total_return": 0.0001},
            "control_metrics": {"total_return": 0.00005},
        }
        for multiplier in COST_MULTIPLIERS
    ]

    statistics = _build_statistics(package, cells)
    comparison = cast(dict[str, Any], statistics["unchanged_incumbent_comparison"])
    assert comparison["id"] == r46_runner.STATISTIC_IDS[1]
    assert comparison["not_tested"] is False
    assert isinstance(comparison["p_value"], (int, float))
    assert comparison["event_episode_count"] == 1
    assert comparison["rows"] == [
        {
            "event_id": schedule.event_id,
            "t0": schedule.entry_decision.isoformat(),
            "t1": schedule.exit_open.isoformat(),
            "candidate_t0": 100_000.0,
            "candidate_t1": 100_010.0,
            "incumbent_t0": 100_000.0,
            "incumbent_t1": 100_005.0,
            "candidate_change": 0.0001,
            "incumbent_change": 0.00005,
            "delta": 0.00005,
        }
    ]
    missing_endpoint_cells = copy.deepcopy(cells)
    missing_endpoint_cells[0]["control_ledger"]["equity_curve_timestamps"] = [
        schedule.entry_decision.isoformat(),
        schedule.entry_open.isoformat(),
        schedule.exit_decision.isoformat(),
    ]
    with pytest.raises(ValueError, match="incumbent:.*(curve_misaligned|endpoint_missing_or_duplicate)"):
        _build_statistics(package, missing_endpoint_cells)


def test_v5_incumbent_dependencies_require_exact_manifest_hashes_and_cutoff(tmp_path: Path) -> None:
    bars = tmp_path / "bars.csv"
    lifetimes = tmp_path / "lifetimes.csv"
    corporate_actions = tmp_path / "corporate_actions.csv"
    bars_manifest = tmp_path / "bars.manifest.json"
    corporate_actions_manifest = tmp_path / "corporate_actions.manifest.json"
    seam = tmp_path / "run_symmetric_v5_validation.py"
    bars.write_bytes(b"bars\n")
    lifetimes.write_bytes(b"lifetimes\n")
    corporate_actions.write_bytes(b"corporate-actions\n")
    seam.write_bytes(b"seam\n")
    bars_manifest.write_text(
        json.dumps(
            {
                "dataset": str(bars),
                "sha256": hashlib.sha256(bars.read_bytes()).hexdigest(),
                "byte_size": bars.stat().st_size,
                "last_date": "2026-07-31T04:00:00Z",
                "lifetime_table": str(lifetimes),
                "lifetime_table_hash": hashlib.sha256(lifetimes.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    corporate_actions_manifest.write_text(
        json.dumps(
            {
                "dataset": str(corporate_actions),
                "sha256": hashlib.sha256(corporate_actions.read_bytes()).hexdigest(),
                "byte_size": corporate_actions.stat().st_size,
                "last_ex_date": "2026-07-31",
            }
        ),
        encoding="utf-8",
    )
    source_paths = {
        "bars": str(bars),
        "bars_sha256": hashlib.sha256(bars.read_bytes()).hexdigest(),
        "bars_manifest": str(bars_manifest),
        "bars_manifest_sha256": hashlib.sha256(bars_manifest.read_bytes()).hexdigest(),
        "lifetimes": str(lifetimes),
        "lifetimes_sha256": hashlib.sha256(lifetimes.read_bytes()).hexdigest(),
        "corporate_actions": str(corporate_actions),
        "corporate_actions_sha256": hashlib.sha256(corporate_actions.read_bytes()).hexdigest(),
        "corporate_actions_manifest": str(corporate_actions_manifest),
        "corporate_actions_manifest_sha256": hashlib.sha256(corporate_actions_manifest.read_bytes()).hexdigest(),
        "seam": str(seam),
        "seam_sha256": hashlib.sha256(seam.read_bytes()).hexdigest(),
        "historical_cutoff": "2026-07-31T23:59:59Z",
    }
    checked = _validate_v5_incumbent_dependencies(source_paths)
    assert checked["historical_cutoff"] == "2026-07-31T23:59:59Z"

    bars.write_bytes(b"tampered\n")
    with pytest.raises(RuntimeError, match="BLOCKED_PIT_SOURCE"):
        _validate_v5_incumbent_dependencies(source_paths)


def test_primary_null_is_explicit_zero_rate_same_reservation_and_cost_cell_is_bound(
    tmp_path: Path,
) -> None:
    package, _incumbent = _native_event_fixture(tmp_path)
    empty_ledger: dict[str, object] = {
        "cost_multiplier": 1.0,
        "event_records": [],
        "fills": [],
        "dividend_events": [],
    }
    cells = [
        {
            "cost_multiplier": multiplier,
            "candidate_ledger": {**empty_ledger, "cost_multiplier": multiplier},
            "candidate_metrics": {"total_return": 0.0},
            "control_metrics": {"total_return": 0.0},
        }
        for multiplier in COST_MULTIPLIERS
    ]
    statistics = _build_statistics(package, cells)
    primary = statistics["primary_estimand"]
    assert primary["estimand"] == "initial_equity_portfolio_contribution"
    assert primary["initial_equity_denominator"] == 100_000.0
    assert primary["null_cash_rate_annualized"] == 0.0
    assert primary["null_reservation_model"] == "cash_reserved_same_notional_zero_rate"
    assert primary["null_reservation_proof"] is True
    assert primary["null_ledger"]["same_reservation_decisions"] is True
    assert len(primary["null_ledger"]["rows"]) == len(primary["rows"])

    mismatched = {**empty_ledger, "cost_multiplier": 2.0}
    with pytest.raises(ValueError, match="cost_multiplier_ledger_mismatch"):
        r46_runner._event_episode_deltas(package, mismatched, cost_multiplier=1.0)


def test_shared_account_rejects_frozen_cap_overrides_and_seed_slot_overflow() -> None:
    with pytest.raises(ValueError, match="frozen_caps"):
        SharedAccountLedger(caps={"combined_gross_max": 0.80})
    ledger = SharedAccountLedger()
    for index in range(6):
        ledger.seed_position(f"V3{index}", qty=1.0, price=100.0, owner="incumbent_v3")
    with pytest.raises(ValueError, match="total_positions_max"):
        ledger.seed_position("V3-overflow", qty=1.0, price=100.0, owner="incumbent_v3")


def test_shared_account_sells_before_buys_and_reconciles_cash_fees_and_ownership() -> None:
    ledger = SharedAccountLedger(initial_cash=100_000.0)
    ledger.seed_position("V3A", qty=550.0, price=100.0, owner="incumbent_v3")
    payload = ledger.run_cycle(
        decision_timestamp=TS,
        signal_asof_timestamp=TS,
        target_weights=(
            {"symbol": "V3A", "weight": 0.45, "owner": "incumbent_v3"},
            {"symbol": "TLT", "weight": 0.025, "owner": "r46"},
        ),
        fills=(
            _fill("TLT", "BUY", 25.0, 100.0, "r46", fee=1.0),
            _fill("V3A", "SELL", 100.0, 100.0, "incumbent_v3", fee=1.0),
        ),
        marks={"V3A": 100.0, "TLT": 100.0},
    )
    assert [row["side"] for row in payload["fills"]] == ["SELL", "BUY"]
    snapshot = payload["snapshots"][-1]
    assert snapshot["cash"] == pytest.approx(52_498.0)
    assert snapshot["equity"] == pytest.approx(99_998.0)
    assert snapshot["fees"] == pytest.approx(2.0)
    assert snapshot["positions"]["V3A"]["owner"] == "incumbent_v3"
    assert snapshot["positions"]["TLT"]["owner"] == "r46"
    assert payload["ownership_attribution"] == {"incumbent_v3": 1, "r46": 1}


def test_fill_rejections_are_persisted_and_batch_is_atomic() -> None:
    ledger = SharedAccountLedger(initial_cash=100_000.0)
    ledger.seed_position("TLT", qty=10.0, price=100.0, owner="r46")
    before = copy.deepcopy(ledger.payload())
    applied = ledger.apply_fills(
        timestamp=TS,
        fills=(
            _fill("TLT", "SELL", 1.0, 100.0, "incumbent_v3"),
            _fill("TLT", "BUY", 1.0, 100.0, "incumbent_v3"),
        ),
    )
    assert applied == ()
    after = ledger.payload()
    assert after["cash"] == before["cash"]
    assert after["positions"] == before["positions"]
    assert [row["side"] for row in after["rejected_orders"]] == ["SELL", "BUY"]
    assert after["rejected_orders"][0]["reason"] == "owner_mismatch"
    assert after["rejected_orders"][1]["reason"] == "mixed_symbol_ownership"


def test_dividend_accounting_is_explicit_and_owner_bound() -> None:
    ledger = SharedAccountLedger(initial_cash=100_000.0)
    ledger.seed_position("TLT", qty=10.0, price=100.0, owner="r46")
    event = ledger.apply_dividend(
        timestamp=TS,
        symbol="TLT",
        per_share=2.0,
        event_id="dividend-1",
    )
    assert event["credited_cash"] == pytest.approx(20.0)
    assert ledger.cash == pytest.approx(99_020.0)
    assert ledger.payload()["dividend_total"] == pytest.approx(20.0)


def test_suppressed_and_duplicate_events_are_persisted() -> None:
    ledger = SharedAccountLedger()
    first = ledger.plan_decision(
        decision_timestamp=TS,
        signal_asof_timestamp=TS,
        target_weights=({"symbol": "TLT", "weight": 0.025, "owner": "r46"},),
        candidate_event_id="auction-1",
        candidate_active=False,
    )
    duplicate = ledger.plan_decision(
        decision_timestamp=TS2,
        signal_asof_timestamp=TS2,
        target_weights=({"symbol": "TLT", "weight": 0.025, "owner": "r46"},),
        candidate_event_id="auction-1",
        candidate_active=True,
    )
    assert first["suppression_reason"] == "missing_event_no_catch_up"
    assert duplicate["suppression_reason"] == "no_pyramid"
    assert [row["event_id"] for row in ledger.payload()["event_records"]] == ["auction-1", "auction-1"]


def test_r46_event_uses_next_open_entry_and_fifth_open_after_entry_exit() -> None:
    session_dates = ("2025-01-16", "2025-01-17", "2025-01-21", "2025-01-22", "2025-01-23", "2025-01-24")
    sessions = [
        {
            "session_date": session_date,
            "regular_open_timestamp": f"{session_date}T14:30:00+00:00",
        }
        for session_date in session_dates
    ]
    schedule = schedule_r46_holding(
        available_at="2025-01-15T21:00:00+00:00",
        auction_session_date="2025-01-15",
        regular_sessions=sessions,
        availability_policy=POLICY,
        eligibility_policy=ELIGIBILITY,
        same_day=False,
    )
    assert schedule["entry_session_date"] == "2025-01-16"
    assert schedule["exit_session_date"] == "2025-01-24"
    assert schedule["holding_intervals"] == 5


def test_r46_schedule_uses_first_open_strictly_after_available_at_across_dst() -> None:
    sessions = [
        {"session_date": "2024-03-11", "regular_open_timestamp": "2024-03-11T13:30:00Z"},
        {"session_date": "2024-03-12", "regular_open_timestamp": "2024-03-12T13:30:00Z"},
        {"session_date": "2024-03-13", "regular_open_timestamp": "2024-03-13T13:30:00Z"},
        {"session_date": "2024-03-14", "regular_open_timestamp": "2024-03-14T13:30:00Z"},
        {"session_date": "2024-03-15", "regular_open_timestamp": "2024-03-15T13:30:00Z"},
        {"session_date": "2024-03-18", "regular_open_timestamp": "2024-03-18T13:30:00Z"},
        {"session_date": "2024-03-19", "regular_open_timestamp": "2024-03-19T13:30:00Z"},
    ]
    schedule = schedule_r46_holding(
        available_at="2024-03-11T13:30:00Z",
        auction_session_date="2024-03-08",
        regular_sessions=sessions,
        availability_policy=POLICY,
        eligibility_policy=ELIGIBILITY,
        same_day=False,
    )
    assert schedule["entry_session_date"] == "2024-03-12"
    assert schedule["entry_open_timestamp"] == "2024-03-12T13:30:00+00:00"
    assert schedule["exit_session_date"] == "2024-03-19"
    assert schedule["exit_open_timestamp"] == "2024-03-19T13:30:00+00:00"


def test_r46_placebo_shift_preserves_new_york_civil_time_across_dst() -> None:
    sessions = ("2024-03-08", "2024-03-11", "2024-03-12")
    source = datetime(2024, 3, 8, 14, 30, tzinfo=UTC)  # 09:30 America/New_York (EST)

    shifted = r46_runner._r46_shift_timestamp(source, sessions, shift_sessions=1)

    assert shifted == datetime(2024, 3, 11, 13, 30, tzinfo=UTC)  # 09:30 America/New_York (EDT)


def test_r46_placebo_shift_anchors_market_closure_to_next_regular_session() -> None:
    sessions = ("2025-01-08", "2025-01-10", "2025-01-13", "2025-01-14")
    closure_midnight = datetime(2025, 1, 9, 5, 0, tzinfo=UTC)

    shifted = r46_runner._r46_shift_timestamp(closure_midnight, sessions, shift_sessions=1)

    assert shifted == datetime(2025, 1, 13, 5, 0, tzinfo=UTC)
    with pytest.raises(RuntimeError, match="BLOCKED_TIMING_PLACEBO_DATA"):
        r46_runner._r46_shift_timestamp(datetime(2025, 1, 7, 5, 0, tzinfo=UTC), sessions, shift_sessions=1)
    with pytest.raises(RuntimeError, match="BLOCKED_TIMING_PLACEBO_DATA"):
        r46_runner._r46_shift_timestamp(datetime(2025, 1, 15, 5, 0, tzinfo=UTC), sessions, shift_sessions=1)


def test_r46_benchmark_projection_is_asof_and_fails_before_first_source() -> None:
    source = (
        Bar(
            datetime(2025, 1, 8, 14, 30, tzinfo=UTC),
            "SPY",
            "1d",
            100.0,
            100.0,
            100.0,
            100.0,
            1_000_000.0,
            Market.EQUITY,
        ),
        Bar(
            datetime(2025, 1, 10, 14, 30, tzinfo=UTC),
            "SPY",
            "1d",
            110.0,
            110.0,
            110.0,
            110.0,
            1_000_000.0,
            Market.EQUITY,
        ),
    )
    marker_only = datetime(2025, 1, 9, 5, 0, tzinfo=UTC)
    assert r46_runner._benchmark_marks_at_timestamps(
        source,
        (source[0].timestamp, marker_only, source[1].timestamp),
    ) == (100.0, 100.0, 110.0)
    with pytest.raises(ValueError, match="benchmark_mark_before_source"):
        r46_runner._benchmark_marks_at_timestamps(
            source,
            (datetime(2025, 1, 8, 5, 0, tzinfo=UTC),),
        )


@pytest.mark.parametrize(
    ("bad_session", "bad_open", "expected"),
    [
        ("2024-03-09", "2024-03-09T14:30:00Z", "regular_session_not_nyse"),
        ("2024-07-04", "2024-07-04T13:30:00Z", "regular_session_not_nyse"),
        ("2024-03-11", "2024-03-11T14:30:00Z", "regular_open_schedule_mismatch"),
        ("2024-03-11", "2024-03-11T08:30:00-05:00", "regular_open_timestamp_offset_invalid"),
    ],
)
def test_r46_schedule_rejects_weekend_holiday_and_forged_dst_open(
    bad_session: str, bad_open: str, expected: str
) -> None:
    sessions = [
        {"session_date": bad_session, "regular_open_timestamp": bad_open},
        {"session_date": "2024-03-12", "regular_open_timestamp": "2024-03-12T13:30:00Z"},
        {"session_date": "2024-03-13", "regular_open_timestamp": "2024-03-13T13:30:00Z"},
        {"session_date": "2024-03-14", "regular_open_timestamp": "2024-03-14T13:30:00Z"},
        {"session_date": "2024-03-15", "regular_open_timestamp": "2024-03-15T13:30:00Z"},
        {"session_date": "2024-03-18", "regular_open_timestamp": "2024-03-18T13:30:00Z"},
        {"session_date": "2024-03-19", "regular_open_timestamp": "2024-03-19T13:30:00Z"},
    ]
    with pytest.raises(ValueError, match=expected):
        schedule_r46_holding(
            available_at="2024-03-08T21:00:00Z",
            auction_session_date="2024-03-08",
            regular_sessions=sessions,
            availability_policy=POLICY,
            eligibility_policy=ELIGIBILITY,
            same_day=False,
        )


def test_r46_policy_fixture_has_127_canonical_events_and_published_at_is_noncausal() -> None:
    events = sorted(
        [
            {
                "canonical_event_id": f"2024-02-{index + 1:02d}-30Y-ORIGINAL",
                "auction_session_date": f"2024-02-{index + 1:02d}",
                "available_at": "2024-02-09T05:00:00Z",
                "published_at": None if index < 15 else "2024-02-08T20:00:00Z",
                "availability_derivation": POLICY,
                "result_signal_eligibility": ELIGIBILITY,
                "same_day_signal_allowed": False,
            }
            for index in range(127)
        ],
        key=lambda event: event["canonical_event_id"],
    )
    publication_policy = {
        "result_availability_policy": POLICY,
        "result_signal_eligibility": ELIGIBILITY,
        "same_day_signal_allowed": False,
        "expected_event_count": 127,
        "expected_event_ids": sorted(event["canonical_event_id"] for event in events),
    }
    package = {
        "expected_event_count": 127,
        "expected_event_ids": sorted(event["canonical_event_id"] for event in events),
        "publication_availability_policy": publication_policy,
    }
    governance = {"publication_availability_policy": publication_policy}
    validated = r46_runner._validate_r46_policy_contract(package, governance, events)
    assert validated == 127
    assert sum(event["published_at"] is None for event in events) == 15
    assert sum(event["published_at"] is not None for event in events) == 112


def test_r46_package_loader_validates_uniform_policy_for_compact_127_event_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.research import build_r46_v2_data_package_2026_09_10 as package_builder
    from scripts.research import r46_treasury_auction_readiness_2026_09_10 as readiness

    root = tmp_path / "repo"
    root.mkdir()
    package_path = root / "package.json"
    governance_path = root / "governance.json"
    event_policy = {
        "availability_derivation": POLICY,
        "result_signal_eligibility": ELIGIBILITY,
        "same_day_signal_allowed": False,
    }

    event_ids = sorted(f"2024-02-{index + 1:02d}-30Y-ORIGINAL" for index in range(127))
    publication_policy = {
        "result_availability_policy": POLICY,
        "result_signal_eligibility": ELIGIBILITY,
        "same_day_signal_allowed": False,
        "expected_event_count": 127,
        "expected_event_ids": event_ids,
    }
    governance_path.write_text(
        json.dumps({"publication_availability_policy": publication_policy}) + "\n", encoding="utf-8"
    )
    events = [
        {
            "canonical_event_id": event_id,
            "security_identifier": "912810TM2",
            "auction_session_date": "2024-02-08",
            "available_at": "2024-02-09T05:00:00Z",
            "entry_available_at": "2024-02-09T05:00:00Z",
            "published_at": None if index < 15 else "2024-02-08T20:00:00Z",
            **event_policy,
        }
        for index, event_id in enumerate(event_ids)
    ]
    documents_path = root / "documents.jsonl"
    documents_path.write_text("", encoding="utf-8")
    bars_path = root / "bars.csv"
    bars_path.write_text(
        "permanent_security_identifier,session_date,regular_open_timestamp,available_at,open,high,low,close,volume\n"
        "sec:tlt,2024-02-08,2024-02-08T14:30:00Z,2024-02-08T21:00:00Z,100,101,99,100,1000000\n",
        encoding="utf-8",
    )
    dividends_path = root / "dividends.csv"
    dividends_path.write_text(
        "permanent_security_identifier,action_type,effective_date,cash_or_ratio,source_document_hash,"
        "duplicate_event_key,event_timestamp,currency,available_at\n",
        encoding="utf-8",
    )
    eligibility_path = root / "eligibility.jsonl"
    eligibility_path.write_text(json.dumps({"permanent_security_identifier": "sec:tlt"}) + "\n", encoding="utf-8")
    calendar_path = root / "calendar.jsonl"
    calendar_rows = [
        {"regular_session_date": day, "regular_open_timestamp": f"{day}T14:30:00Z", "holiday_or_closure_reason": None}
        for day in (
            "2024-02-08",
            "2024-02-09",
            "2024-02-12",
            "2024-02-13",
            "2024-02-14",
            "2024-02-15",
            "2024-02-16",
            "2024-02-20",
        )
    ]
    calendar_path.write_text("".join(json.dumps(row) + "\n" for row in calendar_rows), encoding="utf-8")

    def artifact(path: Path) -> dict[str, str]:
        return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    package: dict[str, object] = {
        "schema": "r46-treasury-auction-unwind-data-package-v2",
        "status": "DATA_PACKAGE_READY",
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "expected_event_count": 127,
        "expected_event_ids": event_ids,
        "governance_manifest_path": governance_path.name,
        "publication_availability_policy": publication_policy,
        "integrity": {"manifest_sha256": "a" * 64},
        "artifacts": {
            "documents": artifact(documents_path),
            "bars": artifact(bars_path),
            "dividends": artifact(dividends_path),
            "eligibility": artifact(eligibility_path),
            "calendar": artifact(calendar_path),
        },
    }
    package_path.write_text(json.dumps(package) + "\n", encoding="utf-8")

    monkeypatch.setattr(package_builder, "consume_r46_v2_data_package", lambda *_args, **_kwargs: package)
    monkeypatch.setattr(package_builder, "_documents_for_validator", lambda rows, **_kwargs: rows)
    monkeypatch.setattr(readiness, "normalize_auction_documents", lambda _rows, **_kwargs: {"events": events})

    loaded = r46_runner.load_r46_v2_package(package_path, repo_root=root)
    assert [schedule.event_id for schedule in loaded.schedules] == event_ids
    assert len(loaded.schedules) == 127
    assert loaded.schedules[0].entry_session_date == "2024-02-09"
    assert loaded.schedules[0].exit_session_date == "2024-02-16"
    assert sum(event["published_at"] is None for event in loaded.events) == 15
    assert sum(event["published_at"] is not None for event in loaded.events) == 112


def test_r46_package_bar_loader_requires_pit_availability_at_or_after_session_open() -> None:
    row = {
        "session_date": "2024-02-09",
        "regular_open_timestamp": "2024-02-09T14:30:00Z",
        "available_at": "2024-02-09T14:29:59Z",
        "open": "100",
        "high": "101",
        "low": "99",
        "close": "100",
        "volume": "1000000",
    }

    with pytest.raises(ValueError, match="bar_available_before_session_open"):
        r46_runner._bar_from_package_row(row)


def test_r46_package_dividend_loader_rejects_availability_after_event_timestamp() -> None:
    row = {
        "permanent_security_identifier": "sec:tlt",
        "action_type": "cash_dividend",
        "currency": "USD",
        "event_timestamp": "2024-02-01T14:30:00Z",
        "available_at": "2024-02-01T14:30:01Z",
        "cash_or_ratio": "0.28",
    }

    with pytest.raises(ValueError, match="dividend_available_after_event"):
        r46_runner._package_dividend_values(row, "sec:tlt")


@pytest.mark.parametrize(
    "package_mutation",
    [
        {"expected_event_count": 24},
        {"publication_availability_policy": {"result_availability_policy": POLICY}},
    ],
)
def test_r46_policy_fixture_rejects_short_or_incomplete_production_package(
    package_mutation: dict[str, object],
) -> None:
    events = [
        {
            "canonical_event_id": f"event-{index:03d}",
            "available_at": "2024-02-09T05:00:00Z",
            "published_at": None,
            "availability_derivation": POLICY,
            "result_signal_eligibility": ELIGIBILITY,
            "same_day_signal_allowed": False,
        }
        for index in range(127)
    ]
    publication_policy = {
        "result_availability_policy": POLICY,
        "result_signal_eligibility": ELIGIBILITY,
        "same_day_signal_allowed": False,
        "expected_event_count": 127,
        "expected_event_ids": sorted(event["canonical_event_id"] for event in events),
    }
    package: dict[str, object] = {
        "expected_event_count": 127,
        "expected_event_ids": sorted(event["canonical_event_id"] for event in events),
        "publication_availability_policy": publication_policy,
    }
    package.update(package_mutation)
    governance = {"publication_availability_policy": publication_policy}
    with pytest.raises(ValueError):
        r46_runner._validate_r46_policy_contract(package, governance, events)


def test_r46_policy_fixture_rejects_all_missing_release_timestamps() -> None:
    events = [
        {
            "canonical_event_id": f"event-{index:03d}",
            "published_at": None,
            **{
                "availability_derivation": POLICY,
                "result_signal_eligibility": ELIGIBILITY,
                "same_day_signal_allowed": False,
            },
        }
        for index in range(127)
    ]
    publication_policy = {
        "result_availability_policy": POLICY,
        "result_signal_eligibility": ELIGIBILITY,
        "same_day_signal_allowed": False,
        "expected_event_count": 127,
        "expected_event_ids": [event["canonical_event_id"] for event in events],
    }
    package = {
        "expected_event_count": 127,
        "expected_event_ids": [event["canonical_event_id"] for event in events],
        "publication_availability_policy": publication_policy,
    }
    with pytest.raises(ValueError, match="published_at_all_missing"):
        r46_runner._validate_r46_policy_contract(
            package, {"publication_availability_policy": publication_policy}, events
        )


def test_r46_actual_release_timestamp_cannot_advance_policy_entry() -> None:
    sessions = [
        {"session_date": f"2024-02-{day:02d}", "regular_open_timestamp": f"2024-02-{day:02d}T14:30:00Z"}
        for day in (9, 12, 13, 14, 15, 16, 20)
    ]
    schedule = schedule_r46_holding(
        available_at="2024-02-10T05:00:00Z",
        auction_session_date="2024-02-08",
        regular_sessions=sessions,
        availability_policy=POLICY,
        eligibility_policy=ELIGIBILITY,
        same_day=False,
        published_at="2024-02-08T14:00:00Z",
    )
    assert schedule["entry_session_date"] == "2024-02-12"
    assert schedule["entry_open_timestamp"] == "2024-02-12T14:30:00+00:00"
    assert schedule["exit_session_date"] == "2024-02-20"


def test_native_adapter_invokes_real_portfolio_engine_and_dividend_path() -> None:
    result = run_native_adapter(build_native_smoke_dataset(), strategy_name="r46_native_smoke")
    assert result.__class__.__name__ == "PortfolioBacktestResult"
    assert result.fills
    assert result.dividend_total > 0.0


def test_package_event_fixture_uses_sixth_open_dividends_and_three_cost_cells(tmp_path: Path) -> None:
    package, incumbent = _native_event_fixture(tmp_path)
    dataset = build_r46_market_dataset(package, incumbent)
    for multiplier in COST_MULTIPLIERS:
        strategy = R46OverlayStrategy(package.schedules)
        result = _run_r46_engine(
            dataset,
            strategy,
            cost_multiplier=multiplier,
            starting_equity=100_000.0,
            config=None,
            universe=None,
            universe_version="fixture",
            event_boundaries=(package.schedules[0].entry_decision, package.schedules[0].exit_decision),
        )
        tlt_fills = [fill for fill in result.fills if fill.symbol == "TLT"]
        assert any(fill.side.value == "buy" and fill.timestamp == package.schedules[0].entry_open for fill in tlt_fills)
        assert any(fill.side.value == "sell" and fill.timestamp == package.schedules[0].exit_open for fill in tlt_fills)
        assert result.dividend_total > 0.0
        ledger = _economic_ledger_payload(
            dataset,
            result,
            strategy=strategy,
            package=package,
            cost_multiplier=multiplier,
            arm="candidate",
            manifest_sha256="b" * 64,
            gate_sha256="c" * 64,
            universe_status="STATIC_TEST_CONTEXT",
            universe_version="fixture",
        )
        assert verify_payload(ledger)["status"] == "VERIFIED"


def test_overlay_suppresses_when_six_incumbent_slots_are_occupied(monkeypatch: pytest.MonkeyPatch) -> None:
    event_timestamp = datetime(2025, 1, 15, 14, 0, tzinfo=UTC)
    schedule = R46EventSchedule(
        "six-slot-event",
        "TLT-TEST",
        "2025-01-15",
        event_timestamp,
        "2025-01-16",
        datetime(2025, 1, 16, 14, 30, tzinfo=UTC),
        "2025-01-21",
        datetime(2025, 1, 21, 14, 30, tzinfo=UTC),
        event_timestamp,
        datetime(2025, 1, 20, 14, 30, tzinfo=UTC),
        5,
        tuple(datetime(2025, 1, 16 + index, 14, 30, tzinfo=UTC) for index in range(6)),
    )
    base_target = TargetPortfolio(
        strategy_name="equity_residual_momentum_v3",
        strategy_version="3.0.0",
        decision_timestamp=event_timestamp,
        weights=(TargetWeight("AAA", 0.05),),
        cash_weight=0.95,
    )
    monkeypatch.setattr(r46_runner.EquityResidualMomentum1dV3, "decide", lambda self, context: base_target)
    strategy = R46OverlayStrategy((schedule,))
    context = PortfolioContext(
        decision_timestamp=event_timestamp,
        market=Market.EQUITY,
        timeframe="1d",
        history={},
        universe=None,
        universe_version="fixture",
        equity=100_000.0,
        holdings={f"SLOT{index}": 1.0 for index in range(6)},
        prices={f"SLOT{index}": 100.0 for index in range(6)},
        benchmark_symbol="SPY",
    )
    strategy.decide(replace(context, decision_timestamp=event_timestamp - timedelta(days=7)))
    target = strategy.decide(context)
    assert not any(weight.symbol == "TLT" for weight in target.weights)
    assert strategy.event_records[-1]["status"] == "SUPPRESSED"
    assert strategy.event_records[-1]["suppression_reason"] == "total_positions_max"


def test_overlay_counts_six_base_targets_before_adding_tlt(monkeypatch: pytest.MonkeyPatch) -> None:
    event_timestamp = datetime(2025, 1, 15, 14, 0, tzinfo=UTC)
    schedule = R46EventSchedule(
        "six-target-event",
        "TLT-TEST",
        "2025-01-15",
        event_timestamp,
        "2025-01-16",
        datetime(2025, 1, 16, 14, 30, tzinfo=UTC),
        "2025-01-21",
        datetime(2025, 1, 21, 14, 30, tzinfo=UTC),
        event_timestamp,
        datetime(2025, 1, 20, 14, 30, tzinfo=UTC),
        5,
        tuple(datetime(2025, 1, 16 + index, 14, 30, tzinfo=UTC) for index in range(6)),
    )
    base_target = TargetPortfolio(
        strategy_name="equity_residual_momentum_v3",
        strategy_version="3.0.0",
        decision_timestamp=event_timestamp,
        weights=tuple(TargetWeight(f"BASE{index}", 0.05) for index in range(6)),
        cash_weight=0.70,
    )
    monkeypatch.setattr(r46_runner.EquityResidualMomentum1dV3, "decide", lambda self, context: base_target)
    strategy = R46OverlayStrategy((schedule,))
    context = PortfolioContext(
        decision_timestamp=event_timestamp,
        market=Market.EQUITY,
        timeframe="1d",
        history={},
        universe=None,
        universe_version="fixture",
        equity=100_000.0,
        holdings={},
        prices={},
        benchmark_symbol="SPY",
    )

    strategy.decide(replace(context, decision_timestamp=event_timestamp - timedelta(days=7)))
    target = strategy.decide(context)

    assert not any(weight.symbol == "TLT" for weight in target.weights)
    assert tuple(weight.symbol for weight in target.weights) == tuple(f"BASE{index}" for index in range(6))
    assert strategy.event_records[-1]["status"] == "SUPPRESSED"
    assert strategy.event_records[-1]["suppression_reason"] == "total_positions_max"


def test_package_runner_round_trip_publishes_consumed_receipt_and_refuses_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package, incumbent = _native_event_fixture(tmp_path)

    state: dict[str, object] = {
        "schema_version": "r46-one-shot-state-v2",
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "status": "SEALED_UNUSED",
        "integrity": {"artifact_sha256": ""},
    }
    state["integrity"]["artifact_sha256"] = _canonical(state, "artifact_sha256")  # type: ignore[index]
    state_path = tmp_path / "state.json"
    _write_json(state_path, state)

    manifest: dict[str, object] = {
        "schema_version": "r46-governance-manifest-v2",
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "evaluation_contract": {
            "one_shot_state_path": "state.json",
            "one_shot_state_sha256": state["integrity"]["artifact_sha256"],  # type: ignore[index]
        },
        "integrity": {"manifest_sha256": ""},
    }
    manifest["integrity"]["manifest_sha256"] = _canonical(manifest, "manifest_sha256")  # type: ignore[index]
    manifest_path = tmp_path / "manifest.json"
    _write_json(manifest_path, manifest)

    package_payload: dict[str, object] = {
        "schema": "r46-treasury-auction-unwind-data-package-v2",
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "integrity": {"manifest_sha256": ""},
    }
    package_payload["integrity"]["manifest_sha256"] = _canonical(package_payload, "manifest_sha256")  # type: ignore[index]
    package_path = tmp_path / "package.json"
    _write_json(package_path, package_payload)
    package = r46_runner.R46PackageInputs(
        package_path=package_path,
        root=tmp_path,
        package={"integrity": {"manifest_sha256": package_payload["integrity"]["manifest_sha256"]}},  # type: ignore[index]
        documents=package.documents,
        events=package.events,
        tlt_bars=package.tlt_bars,
        tlt_dividends=package.tlt_dividends,
        eligibility=package.eligibility,
        calendar=package.calendar,
        schedules=package.schedules,
        markers=package.markers,
        marker_sources=package.marker_sources,
    )

    gate: dict[str, object] = {
        "schema_version": "r46-v2-preflight-receipt-v2",
        "status": PRE_RUN_OK,
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "manifest_path": "manifest.json",
        "package_path": "package.json",
        "manifest_sha256": manifest["integrity"]["manifest_sha256"],  # type: ignore[index]
        "package_sha256": package_payload["integrity"]["manifest_sha256"],  # type: ignore[index]
        "source_commit": "a" * 40,
        "freeze_commit": "b" * 40,
        "research_only": True,
        "read_only": True,
        "economic_execution": False,
        "economic_run": False,
        "economic_outcomes_accessed": False,
        "outcome_artifacts_present": False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "integration_candidate": False,
        "promotion": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
        "integrity": {"receipt_sha256": ""},
    }
    gate["integrity"]["receipt_sha256"] = _canonical(gate, "receipt_sha256")  # type: ignore[index]
    gate_path = tmp_path / "gate.json"
    _write_json(gate_path, gate)

    monkeypatch.setattr(r46_runner, "load_r46_v2_package", lambda *args, **kwargs: package)
    monkeypatch.setattr(r46_runner, "load_incumbent_pit_inputs", lambda **kwargs: incumbent)
    monkeypatch.setattr(
        r46_runner,
        "_manifest_statistics_pins",
        lambda *args, **kwargs: {
            "statistics_plan_path": "fixture",
            "statistics_plan_sha256": "c" * 64,
            "family_registry_path": "fixture",
            "family_registry_sha256": "d" * 64,
            "family_size": 7,
            "reported_output_count": 7,
            "inferential_family_size": 4,
            "inferential_ids": list(r46_runner.INFERENTIAL_STATISTIC_IDS),
            "statistic_ids": list(r46_runner.STATISTIC_IDS),
        },
    )
    gate_validation_calls: list[Path] = []
    original_validate_gate = r46_runner._validate_pre_run_receipt

    def validate_gate(path: Path, *, repo_root: Path | None = None) -> dict[str, Any]:
        gate_validation_calls.append(path)
        return original_validate_gate(path, repo_root=repo_root)

    monkeypatch.setattr(r46_runner, "_validate_pre_run_receipt", validate_gate)
    preflight_calls: list[dict[str, object]] = []

    def fake_persisted_preflight(**kwargs: object) -> dict[str, object]:
        preflight_calls.append(kwargs)
        return {
            "status": PRE_RUN_OK,
            "preflight_receipt": gate,
            "source_commit": "a" * 40,
            "freeze_commit": "b" * 40,
        }

    monkeypatch.setattr(r46_runner, "_verify_persisted_preflight", fake_persisted_preflight)

    output_dir = tmp_path / "result"
    result = r46_runner.run_package_backed_test_only(
        gate=gate_path,
        output_dir=output_dir,
        repo_root=tmp_path,
        incumbent_inputs=incumbent,
        config=None,
    )
    receipt = json.loads((output_dir / "run_receipt.json").read_text(encoding="utf-8"))
    assert result["status"] == "FAILED_VALID_FROZEN_TEST"
    assert Path(receipt["runner_path"]).resolve() == Path(r46_runner.__file__).resolve()
    assert Path(receipt["verifier_path"]).resolve() == (
        REPO / "scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py"
    ).resolve()
    assert Path(receipt["source_path"]).resolve() == (REPO / "src/parayabuck/backtest/portfolio_engine.py").resolve()
    with pytest.raises(RuntimeError, match="BLOCKED_V2_SOURCE_PATH"):
        r46_runner._receipt_source_path(Path(r46_runner.__file__), repo_root=tmp_path, test_only=False)
    results_payload = json.loads((output_dir / "results.json").read_text(encoding="utf-8"))
    ledger_payload = json.loads((output_dir / "ledger.json").read_text(encoding="utf-8"))
    assert results_payload["native_engine"] == "TerminalAwareResearchEngine"
    assert ledger_payload["native_engine"] == "TerminalAwareResearchEngine"
    assert len(preflight_calls) == 2
    assert len(gate_validation_calls) == 2
    assert receipt["one_shot"]["status"] == "CONSUMED"
    assert receipt["outputs"]["bundle_sha256"] == receipt["one_shot"]["output_sha256"]
    assert r46_runner.one_shot_status(state_path) == "CONSUMED"
    assert r46_runner.reconcile_one_shot(state_path, output_dir / "run_receipt.json", require_crosslinks=True) == "CONSUMED"

    consumed_path = state_path.with_name(f"{state_path.name}.consumed.json")
    tampered_consumed = json.loads(consumed_path.read_text(encoding="utf-8"))
    tampered_consumed["gate_sha256"] = "f" * 64
    tampered_consumed["integrity"]["transition_sha256"] = _canonical(tampered_consumed, "transition_sha256")
    consumed_path.write_text(json.dumps(tampered_consumed, sort_keys=True) + "\n", encoding="utf-8")
    assert (
        r46_runner.reconcile_one_shot(state_path, output_dir / "run_receipt.json", require_crosslinks=True)
        == "ABORTED_CONSUMED"
    )

    from scripts.research.verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10 import verify_economic_run

    verified = verify_economic_run(
        json.loads((output_dir / "results.json").read_text(encoding="utf-8")),
        json.loads((output_dir / "ledger.json").read_text(encoding="utf-8")),
        json.loads((output_dir / "report.json").read_text(encoding="utf-8")),
        receipt,
    )
    assert verified["status"] == "VERIFIED_ECONOMIC_RUN"
    tampered_results = json.loads((output_dir / "results.json").read_text(encoding="utf-8"))
    tampered_results.pop("research_only")
    with pytest.raises(ValueError, match="research_only_boundary"):
        verify_economic_run(
            tampered_results,
            json.loads((output_dir / "ledger.json").read_text(encoding="utf-8")),
            json.loads((output_dir / "report.json").read_text(encoding="utf-8")),
        )
    tampered_report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    tampered_report["promotion"] = True
    with pytest.raises(ValueError, match="research_only_boundary"):
        verify_economic_run(
            json.loads((output_dir / "results.json").read_text(encoding="utf-8")),
            json.loads((output_dir / "ledger.json").read_text(encoding="utf-8")),
            tampered_report,
        )
    tampered_receipt = copy.deepcopy(receipt)
    tampered_receipt["auto_promotion"] = True
    with pytest.raises(ValueError, match="research_only_boundary"):
        verify_economic_run(
            json.loads((output_dir / "results.json").read_text(encoding="utf-8")),
            json.loads((output_dir / "ledger.json").read_text(encoding="utf-8")),
            json.loads((output_dir / "report.json").read_text(encoding="utf-8")),
            tampered_receipt,
        )
    with pytest.raises(RuntimeError, match="ONE_SHOT_ALREADY_CONSUMED"):
        r46_runner.run_package_backed_test_only(
            gate=gate_path,
            output_dir=tmp_path / "second-result",
            repo_root=tmp_path,
            incumbent_inputs=incumbent,
            config=None,
        )


def test_native_smoke_attestation_is_independently_verifiable_and_tamper_evident(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    smoke = native_adapter_smoke()
    assert smoke["status"] == "READY_NATIVE_LEDGER"
    attestation, _ = _valid_native_attestation_fixture(tmp_path, monkeypatch)
    assert verify_attestation(attestation, repo_root=tmp_path)["status"] == "VERIFIED_NATIVE_LEDGER"
    tampered = copy.deepcopy(attestation)
    tampered["fills"][0]["qty"] = float(tampered["fills"][0]["qty"]) + 1.0
    with pytest.raises(ValueError, match="ATTESTATION_HASH_MISMATCH"):
        verify_attestation(tampered, repo_root=tmp_path)


def test_native_attestation_rejects_caller_forged_file_pins(tmp_path: Path) -> None:
    def write_artifact(path: Path, schema: str) -> None:
        payload: dict[str, object] = {"schema": schema, "integrity": {"manifest_sha256": ""}}
        payload["integrity"]["manifest_sha256"] = _canonical(payload, "manifest_sha256")  # type: ignore[index]
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    governance = tmp_path / "governance.json"
    package = tmp_path / "package.json"
    write_artifact(governance, "r46-governance-manifest-v2")
    write_artifact(package, "r46-treasury-auction-unwind-data-package-v2")

    with pytest.raises(ValueError, match="ATTESTATION_PIN_MISMATCH"):
        build_attestation(
            governance_manifest_path=governance,
            data_package_manifest_path=package,
            governance_canonical_sha256="a" * 64,
            package_canonical_sha256="b" * 64,
            runner_sha256="c" * 64,
            verifier_sha256="d" * 64,
            source_sha256="e" * 64,
        )


def test_independent_attestation_verifier_recomputes_current_file_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attestation, runner_path = _valid_native_attestation_fixture(tmp_path, monkeypatch)
    assert verify_attestation(attestation, repo_root=tmp_path)["status"] == "VERIFIED_NATIVE_LEDGER"

    runner_path.write_text("runner-tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ATTESTATION_PIN_MISMATCH"):
        verify_attestation(attestation, repo_root=tmp_path)


def test_preflight_rejects_explicit_alternate_evidence_paths(tmp_path: Path) -> None:
    manifest = {
        "schema_version": "r46-governance-manifest-v2",
        "research_id": preflight.V2_RESEARCH_ID,
        "candidate_id": "R46",
        "evidence_contract": {
            "package_path": "package.json",
            "native_attestation_path": "declared-native.json",
            "pit_source_attestation_path": "declared-pit.json",
            "independent_software_review_path": "declared-review.json",
        },
        "data_contract": {
            "data_package_manifest_path": "package.json",
            "pit_source_attestation_path": "declared-pit.json",
        },
        "execution_contract": {"native_ledger_attestation_path": "declared-native.json"},
        "source_provenance": {
            "independent_software_review": {"path": "declared-review.json"},
        },
        "integrity": {"manifest_sha256": ""},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    for name in ("alternate-native.json", "alternate-pit.json", "alternate-review.json"):
        (tmp_path / name).write_text("{}\n", encoding="utf-8")

    result = preflight.verify_v2_manifest(
        manifest_path,
        repo_root=tmp_path,
        native_ledger_attestation_path=tmp_path / "alternate-native.json",
        pit_source_attestation_path=tmp_path / "alternate-pit.json",
        independent_software_review_path=tmp_path / "alternate-review.json",
    )

    assert "NATIVE_LEDGER_EXPLICIT_PATH_MISMATCH" in result["blockers"]
    assert "PIT_SOURCE_EXPLICIT_PATH_MISMATCH" in result["blockers"]
    assert "INDEPENDENT_SOFTWARE_REVIEW_EXPLICIT_PATH_MISMATCH" in result["blockers"]


def test_one_shot_reservation_is_atomic_no_overwrite_and_consumed_once(tmp_path: Path) -> None:
    state: dict[str, Any] = {
        "schema_version": "r46-one-shot-state-v2",
        "research_id": "r46_treasury_auction_unwind_tlt_v2_2026_09_10",
        "candidate_id": "R46",
        "status": "SEALED_UNUSED",
        "integrity": {"artifact_sha256": ""},
    }
    state["integrity"]["artifact_sha256"] = _canonical(state, "artifact_sha256")
    state_path = tmp_path / "one-shot.json"
    _write_json(state_path, state)
    assert reserve_one_shot(state_path, require_crosslinks=False)["status"] == "RESERVED"
    with pytest.raises(RuntimeError, match="ONE_SHOT_ALREADY_RESERVED"):
        reserve_one_shot(state_path, require_crosslinks=False)
    assert consume_one_shot(state_path, "a" * 64, require_crosslinks=False)["status"] == "CONSUMED"
    with pytest.raises(RuntimeError, match="ONE_SHOT_ALREADY_CONSUMED"):
        consume_one_shot(state_path, "b" * 64, require_crosslinks=False)


def test_one_shot_transitions_are_self_hashed_and_crosslinked(tmp_path: Path) -> None:
    state: dict[str, Any] = {
        "schema_version": "r46-one-shot-state-v2",
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "status": "SEALED_UNUSED",
        "integrity": {"artifact_sha256": ""},
    }
    state["integrity"]["artifact_sha256"] = _canonical(state, "artifact_sha256")  # type: ignore[index]
    state_path = tmp_path / "one-shot.json"
    _write_json(state_path, state)
    reserve = reserve_one_shot(
        state_path,
        gate_sha256="a" * 64,
        manifest_sha256="b" * 64,
        package_sha256="c" * 64,
        require_crosslinks=False,
    )
    assert reserve["status"] == "RESERVED"
    reserved_path = state_path.with_name(f"{state_path.name}.reserved.json")
    reserved = json.loads(reserved_path.read_text(encoding="utf-8"))
    assert reserved["schema_version"] == "r46-one-shot-transition-v2"
    assert reserved["state_sha256"] == hashlib.sha256(state_path.read_bytes()).hexdigest()
    assert reserved["gate_sha256"] == "a" * 64
    assert reserved["manifest_sha256"] == "b" * 64
    assert reserved["package_sha256"] == "c" * 64
    assert reserved["integrity"]["transition_sha256"] == _canonical(reserved, "transition_sha256")

    consume_one_shot(
        state_path,
        "d" * 64,
        gate_sha256="a" * 64,
        manifest_sha256="b" * 64,
        package_sha256="c" * 64,
        receipt_path=tmp_path / "run_receipt.json",
        require_crosslinks=False,
    )
    consumed = json.loads(state_path.with_name(f"{state_path.name}.consumed.json").read_text(encoding="utf-8"))
    assert consumed["output_sha256"] == "d" * 64
    assert consumed["integrity"]["transition_sha256"] == _canonical(consumed, "transition_sha256")

    consumed["status"] = "RESERVED"
    consumed_path = state_path.with_name(f"{state_path.name}.consumed.json")
    consumed_path.write_text(json.dumps(consumed, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="ONE_SHOT_TRANSITION_(HASH_INVALID|STATUS_INVALID)"):
        r46_runner.one_shot_status(state_path)


def test_one_shot_crash_reconciliation_never_reopens_consumed_state(tmp_path: Path) -> None:
    state: dict[str, Any] = {
        "schema_version": "r46-one-shot-state-v2",
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "status": "SEALED_UNUSED",
        "integrity": {"artifact_sha256": ""},
    }
    state["integrity"]["artifact_sha256"] = _canonical(state, "artifact_sha256")  # type: ignore[index]
    state_path = tmp_path / "one-shot.json"
    _write_json(state_path, state)
    reserve_one_shot(state_path, require_crosslinks=False)
    consume_one_shot(
        state_path,
        "e" * 64,
        receipt_path=tmp_path / "missing-receipt.json",
        require_crosslinks=False,
    )

    assert (
        r46_runner.reconcile_one_shot(state_path, tmp_path / "missing-receipt.json", require_crosslinks=False)
        == "ABORTED_CONSUMED"
    )
    assert r46_runner.one_shot_status(state_path, require_crosslinks=False) == "ABORTED_CONSUMED"
    with pytest.raises(RuntimeError, match="ONE_SHOT_ALREADY_CONSUMED"):
        reserve_one_shot(state_path, require_crosslinks=False)


def test_run_native_once_is_fail_closed_and_separate_from_test_only_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The compatibility name must never reserve or consume any final state."""

    gate_path = _pinned_gate(tmp_path)
    state: dict[str, Any] = {
        "schema_version": "r46-one-shot-state-v2",
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "status": "SEALED_UNUSED",
        "integrity": {"artifact_sha256": ""},
    }
    state["integrity"]["artifact_sha256"] = _canonical(state, "artifact_sha256")  # type: ignore[index]
    state_path = tmp_path / "state.json"
    _write_json(state_path, state)
    monkeypatch.setattr(
        r46_runner,
        "run_native_adapter",
        lambda *args, **kwargs: pytest.fail("native adapter must not be reached"),
    )

    with pytest.raises(RuntimeError, match="BLOCKED_TEST_ONLY_NATIVE_RUN"):
        r46_runner.run_native_once(
            dataset=build_native_smoke_dataset(),
            gate=gate_path,
            one_shot_state_path=state_path,
        )

    assert r46_runner.one_shot_status(state_path) == "SEALED_UNUSED"
    assert callable(getattr(r46_runner, "run_native_once_test_only", None))


def test_one_shot_sidecar_persistence_fsyncs_file_and_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    original_fsync = r46_runner.os.fsync
    monkeypatch.setattr(r46_runner.os, "fsync", lambda descriptor: (calls.append(descriptor), original_fsync(descriptor))[1])
    r46_runner._exclusive_json(tmp_path / "sidecar.json", {"status": "RESERVED"})
    assert len(calls) >= 2


def test_production_one_shot_transitions_require_all_crosslinks(tmp_path: Path) -> None:
    state: dict[str, Any] = {
        "schema_version": "r46-one-shot-state-v2",
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "status": "SEALED_UNUSED",
        "integrity": {"artifact_sha256": ""},
    }
    state["integrity"]["artifact_sha256"] = _canonical(state, "artifact_sha256")  # type: ignore[index]
    state_path = tmp_path / "state.json"
    _write_json(state_path, state)
    with pytest.raises(RuntimeError, match="ONE_SHOT_TRANSITION_CROSSLINK_REQUIRED"):
        reserve_one_shot(state_path)


def test_reconciliation_invalid_receipt_aborts_consumed_without_rerun(tmp_path: Path) -> None:
    state: dict[str, Any] = {
        "schema_version": "r46-one-shot-state-v2",
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "status": "SEALED_UNUSED",
        "integrity": {"artifact_sha256": ""},
    }
    state["integrity"]["artifact_sha256"] = _canonical(state, "artifact_sha256")  # type: ignore[index]
    state_path = tmp_path / "state.json"
    _write_json(state_path, state)
    receipt_path = tmp_path / "run_receipt.json"
    receipt_path.write_text("{}\n", encoding="utf-8")
    reserve_one_shot(
        state_path,
        gate_sha256="a" * 64,
        manifest_sha256="b" * 64,
        package_sha256="c" * 64,
        require_crosslinks=True,
    )
    consume_one_shot(
        state_path,
        "d" * 64,
        gate_sha256="a" * 64,
        manifest_sha256="b" * 64,
        package_sha256="c" * 64,
        output_path=tmp_path,
        receipt_path=receipt_path,
        require_crosslinks=True,
    )

    assert r46_runner.reconcile_one_shot(state_path, receipt_path, require_crosslinks=True) == "ABORTED_CONSUMED"
    assert r46_runner.one_shot_status(state_path) == "ABORTED_CONSUMED"


def test_reconciliation_malformed_consumed_is_terminal_under_strict_reads(tmp_path: Path) -> None:
    state: dict[str, Any] = {
        "schema_version": "r46-one-shot-state-v2",
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "status": "SEALED_UNUSED",
        "integrity": {"artifact_sha256": ""},
    }
    state["integrity"]["artifact_sha256"] = _canonical(state, "artifact_sha256")  # type: ignore[index]
    state_path = tmp_path / "state.json"
    _write_json(state_path, state)
    receipt_path = tmp_path / "run_receipt.json"
    reserve_one_shot(
        state_path,
        gate_sha256="a" * 64,
        manifest_sha256="b" * 64,
        package_sha256="c" * 64,
        require_crosslinks=True,
    )
    consume_one_shot(
        state_path,
        "d" * 64,
        gate_sha256="a" * 64,
        manifest_sha256="b" * 64,
        package_sha256="c" * 64,
        output_path=tmp_path,
        receipt_path=receipt_path,
        require_crosslinks=True,
    )
    consumed_path = state_path.with_name(f"{state_path.name}.consumed.json")
    malformed = json.loads(consumed_path.read_text(encoding="utf-8"))
    malformed.pop("schema_version", None)
    malformed.pop("state_path", None)
    malformed.pop("state_sha256", None)
    malformed.pop("state_canonical_sha256", None)
    malformed.pop("gate_sha256", None)
    malformed.pop("manifest_sha256", None)
    malformed.pop("package_sha256", None)
    malformed.pop("output_sha256", None)
    malformed["reserved_transition_sha256"] = "not-a-hash"
    malformed["integrity"]["transition_sha256"] = _canonical(malformed, "transition_sha256")
    consumed_path.write_text(json.dumps(malformed, sort_keys=True) + "\n", encoding="utf-8")

    assert r46_runner.reconcile_one_shot(state_path, receipt_path, require_crosslinks=True) == "ABORTED_CONSUMED"
    aborted_path = state_path.with_name(f"{state_path.name}.aborted_consumed.json")
    aborted = json.loads(aborted_path.read_text(encoding="utf-8"))
    assert aborted["schema_version"] == "r46-one-shot-transition-v2"
    assert aborted["state_path"] == str(state_path.resolve())
    assert aborted["state_sha256"] == hashlib.sha256(state_path.read_bytes()).hexdigest()
    assert aborted["reserved_transition_sha256"] is None
    assert r46_runner.reconcile_one_shot(state_path, receipt_path, require_crosslinks=True) == "ABORTED_CONSUMED"
    assert r46_runner.one_shot_status(state_path) == "ABORTED_CONSUMED"
    with pytest.raises(RuntimeError, match="ONE_SHOT_ALREADY_CONSUMED"):
        reserve_one_shot(
            state_path,
            gate_sha256="a" * 64,
            manifest_sha256="b" * 64,
            package_sha256="c" * 64,
        )


def test_reconciliation_never_accepts_receipt_for_malformed_consumed_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state: dict[str, Any] = {
        "schema_version": "r46-one-shot-state-v2",
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "status": "SEALED_UNUSED",
        "integrity": {"artifact_sha256": ""},
    }
    state["integrity"]["artifact_sha256"] = _canonical(state, "artifact_sha256")  # type: ignore[index]
    state_path = tmp_path / "state.json"
    _write_json(state_path, state)
    receipt_path = tmp_path / "run_receipt.json"
    receipt_path.write_text("{}\n", encoding="utf-8")
    reserve_one_shot(
        state_path,
        gate_sha256="a" * 64,
        manifest_sha256="b" * 64,
        package_sha256="c" * 64,
    )
    consume_one_shot(
        state_path,
        "d" * 64,
        gate_sha256="a" * 64,
        manifest_sha256="b" * 64,
        package_sha256="c" * 64,
        output_path=tmp_path,
        receipt_path=receipt_path,
    )
    consumed_path = state_path.with_name(f"{state_path.name}.consumed.json")
    malformed = json.loads(consumed_path.read_text(encoding="utf-8"))
    malformed["schema_version"] = "malformed"
    malformed["integrity"]["transition_sha256"] = _canonical(malformed, "transition_sha256")
    consumed_path.write_text(json.dumps(malformed, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr(r46_runner, "_valid_published_run_receipt", lambda *args: True)

    assert r46_runner.reconcile_one_shot(state_path, receipt_path) == "ABORTED_CONSUMED"
    assert r46_runner.one_shot_status(state_path) == "ABORTED_CONSUMED"


def test_transition_filename_status_and_state_canonical_hash_are_strict(tmp_path: Path) -> None:
    state: dict[str, Any] = {
        "schema_version": "r46-one-shot-state-v2",
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "status": "SEALED_UNUSED",
        "integrity": {"artifact_sha256": ""},
    }
    state["integrity"]["artifact_sha256"] = _canonical(state, "artifact_sha256")  # type: ignore[index]
    state_path = tmp_path / "state.json"
    _write_json(state_path, state)
    reserve_one_shot(state_path, require_crosslinks=False)
    reserved_path = state_path.with_name("state.json.reserved.json")
    consumed_path = state_path.with_name("state.json.consumed.json")
    reserved_path.rename(consumed_path)
    with pytest.raises(RuntimeError, match="ONE_SHOT_TRANSITION_STATUS_INVALID"):
        r46_runner.one_shot_status(state_path)

    consumed_path.rename(reserved_path)
    transition = json.loads(reserved_path.read_text(encoding="utf-8"))
    transition["state_canonical_sha256"] = "0" * 64
    transition["integrity"]["transition_sha256"] = _canonical(transition, "transition_sha256")
    reserved_path.write_text(json.dumps(transition, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="ONE_SHOT_TRANSITION_STATE_CANONICAL_HASH_INVALID"):
        r46_runner.one_shot_status(state_path)


def test_inner_verifier_recomputes_boundaries_and_rejects_tampering() -> None:
    payload = native_adapter_smoke()["ledger"]
    assert verify_payload(payload)["status"] == "VERIFIED"
    tampered = copy.deepcopy(payload)
    tampered["snapshots"][-1]["cash"] = float(tampered["snapshots"][-1]["cash"]) + 1.0
    with pytest.raises(ValueError, match="LEDGER_HASH_MISMATCH|cash_reconciliation"):
        verify_payload(tampered)


def test_inner_verifier_recomputes_fill_costs_and_snapshot_chain() -> None:
    payload = native_adapter_smoke()["ledger"]
    tampered_fill = copy.deepcopy(payload)
    tampered_fill["fills"][0]["reference_price"] = float(tampered_fill["fills"][0]["reference_price"]) + 1.0
    tampered_fill["ledger_sha256"] = r46_runner._canonical_hash(tampered_fill, "ledger_sha256")
    with pytest.raises(ValueError, match="fill_cost_reconciliation"):
        verify_payload(tampered_fill)

    tampered_snapshot = copy.deepcopy(payload)
    tampered_snapshot["snapshots"][-1]["gross_exposure"] = 0.99
    tampered_snapshot["ledger_sha256"] = r46_runner._canonical_hash(tampered_snapshot, "ledger_sha256")
    with pytest.raises(ValueError, match="snapshot_cap_violation|snapshot_reconciliation"):
        verify_payload(tampered_snapshot)


def test_economic_structural_verifier_requires_inner_ledger_hash_before_replay() -> None:
    """A direct structural call must not be an unhashed bypass of verify_payload."""

    payload = copy.deepcopy(native_adapter_smoke()["ledger"])
    payload.update(
        {
            "status": "R46_V2_ECONOMIC_RUN",
            "native_engine": "TerminalAwareResearchEngine",
            "arm": "candidate",
            "control": False,
            "cost_multiplier": 1.0,
            "manifest_canonical_sha256": "a" * 64,
            "package_canonical_sha256": "b" * 64,
            "preflight_receipt_sha256": "c" * 64,
            "dataset_digest": "d" * 64,
            "decisions": [],
            "event_records": [],
            "event_fill_statuses": [],
            "event_schedules": [],
        }
    )
    payload.pop("ledger_sha256")
    with pytest.raises(ValueError, match="LEDGER_HASH_MISMATCH"):
        _verify_economic_structural(payload)


def test_economic_payload_does_not_publish_redundant_residual_arrays(tmp_path: Path) -> None:
    """Economic replay derives residuals from trusted fills, not copied arrays."""

    dataset = r46_runner.build_native_smoke_dataset()
    result = r46_runner.run_native_adapter(
        dataset,
        strategy_name=r46_runner.TEST_NATIVE_STRATEGY,
    )
    package = R46PackageInputs(
        package_path=tmp_path / "package.json",
        root=tmp_path,
        package={"integrity": {"manifest_sha256": "a" * 64}, "artifacts": {}},
        documents=(),
        events=(),
        tlt_bars=(),
        tlt_dividends=(),
        eligibility=(),
        calendar=(),
        schedules=(),
        markers=(),
    )
    payload = _economic_ledger_payload(
        dataset,
        result,
        strategy=None,
        package=package,
        cost_multiplier=1.0,
        arm="control",
        manifest_sha256="b" * 64,
        gate_sha256="c" * 64,
        universe_status="TEST_ONLY",
        universe_version="TEST_ONLY",
    )
    assert "rejected_orders" not in payload
    assert "unfilled_exits" not in payload
    for rebalance in payload["rebalances"]:
        assert not {
            "target_weights",
            "reason",
            "universe_version",
            "universe_snapshot_id",
            "risk_overlay_halted",
            "risk_overlay_reasons",
        } & set(rebalance)
    for decision in payload["decisions"]:
        assert "rebalance_reason" not in decision
    if payload["rebalances"]:
        for field in (
            "target_weights",
            "reason",
            "universe_version",
            "universe_snapshot_id",
            "risk_overlay_halted",
            "risk_overlay_reasons",
        ):
            tampered_metadata = copy.deepcopy(payload)
            tampered_metadata["rebalances"][0][field] = "forged"  # type: ignore[index]
            tampered_metadata["ledger_sha256"] = r46_runner._canonical_hash(
                tampered_metadata,
                "ledger_sha256",
            )
            with pytest.raises(ValueError, match="economic_rebalance_metadata_forbidden"):
                _verify_economic_structural(tampered_metadata)

    tampered = copy.deepcopy(payload)
    tampered["rejected_orders"] = []
    tampered["ledger_sha256"] = r46_runner._canonical_hash(tampered, "ledger_sha256")
    with pytest.raises(ValueError, match="economic_residual_arrays_forbidden"):
        _verify_economic_structural(tampered)


def test_decision_contract_preserves_desired_overlay_separately_from_applied_target() -> None:
    record = type(
        "Record",
        (),
        {
            "decision_timestamp": TS,
            "target_weights": (("V3A", 0.5),),
            "target_cash_weight": 0.5,
            "reason": "weekly",
            "unfilled_notional": 0.0,
            "rejected_notional": 0.0,
        },
    )()
    result = type("Result", (), {"rebalances": (record,)})()
    decisions = r46_runner._rebalances_to_decisions(
        result,
        desired_overlay_weights={TS: (("TLT", 0.025),)},
    )
    assert decisions[0]["target_weights"] == [
        {
            "symbol": "V3A",
            "weight": 0.5,
            "owner": "incumbent_v3",
            "reason": "unchanged_incumbent_v3",
        }
    ]
    assert decisions[0]["desired_target_weights"] == [
        {
            "symbol": "V3A",
            "weight": 0.5,
            "owner": "incumbent_v3",
            "reason": "unchanged_incumbent_v3",
        },
        {
            "symbol": "TLT",
            "weight": 0.025,
            "owner": "r46",
            "reason": "r46_event_episode",
        },
    ]
    assert decisions[0]["desired_target_cash_weight"] == pytest.approx(0.475)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("status", "PENDING", "fill_status_invalid"),
        ("notional", 1.0, "fill_notional_reconciliation"),
        ("decision_timestamp", "2025-01-04T14:30:00+00:00", "fill_decision_after_execution"),
    ),
)
def test_native_fill_identity_and_causality_are_recomputed(
    field: str, value: object, error: str
) -> None:
    payload = copy.deepcopy(native_adapter_smoke()["ledger"])
    payload["fills"][0][field] = value  # type: ignore[index]
    payload["ledger_sha256"] = r46_runner._canonical_hash(payload, "ledger_sha256")
    with pytest.raises(ValueError, match=error):
        verify_payload(payload)


def test_native_snapshot_gross_and_cash_weight_are_recomputed() -> None:
    payload = copy.deepcopy(native_adapter_smoke()["ledger"])
    snapshot = payload["snapshots"][-1]
    snapshot["gross_exposure"] = 0.0
    snapshot["cash_weight"] = 1.0
    payload["ledger_sha256"] = r46_runner._canonical_hash(payload, "ledger_sha256")
    with pytest.raises(ValueError, match="snapshot_(gross|cash_weight)_reconciliation"):
        verify_payload(payload)


def test_public_economic_package_verifier_requires_published_files_and_receipt(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="economic_output_files_missing"):
        r46_verifier.verify_economic_package(tmp_path)


def test_strict_curve_rejects_unbound_empty_benchmark_curve() -> None:
    """A serialized result must carry the complete pinned SPY mark series."""

    inner: dict[str, Any] = {
        "result_metrics": {
            "starting_equity": 100_000.0,
            "ending_equity": 101_000.0,
            "total_return": 0.01,
            "net_return": 0.01,
            "max_drawdown": 0.0,
            "duration_days": 1,
            "benchmark_return": 0.0,
            "excess_return": 0.01,
        },
        "equity_curve": [100_000.0, 101_000.0],
        "equity_curve_timestamps": [TS.isoformat(), TS2.isoformat()],
        "benchmark_curve": [],
        "benchmark_curve_timestamps": [],
    }
    with pytest.raises(ValueError, match="strict_benchmark_curve_missing"):
        r46_verifier._strict_recompute_curve(inner)


def test_strict_curve_recomputes_all_serialized_result_metrics() -> None:
    """Rehashing a ledger must not make ignored risk/economics fields authoritative."""

    inner: dict[str, Any] = {
        "result_metrics": {
            "starting_equity": 100_000.0,
            "ending_equity": 101_000.0,
            "total_return": 0.01,
            "gross_return": 0.01,
            "net_return": 0.01,
            "max_drawdown": 0.0,
            "cagr": 999.0,
            "annualized_volatility": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "turnover": 0.0,
            "fee_total": 0.0,
            "slippage_cost_total": 0.0,
            "spread_cost_total": 0.0,
            "dividend_total": 0.0,
            "average_holdings_count": 0.0,
            "average_cash_ratio": 1.0,
            "maximum_concentration": 0.0,
            "fill_count": 0,
            "rebalance_count": 0,
            "closed_positions": 0,
            "duration_days": 1,
            "benchmark_return": 0.01,
            "excess_return": 0.0,
        },
        "equity_curve": [100_000.0, 101_000.0],
        "equity_curve_timestamps": [TS.isoformat(), TS2.isoformat()],
        "benchmark_curve": [100.0, 101.0],
        "benchmark_curve_timestamps": [TS.isoformat(), TS2.isoformat()],
        "fills": [],
        "rebalances": [],
        "dividend_events": [],
        "snapshots": [
            {"timestamp": TS.isoformat(), "cash": 100_000.0, "equity": 100_000.0, "positions": {}},
            {"timestamp": TS2.isoformat(), "cash": 101_000.0, "equity": 101_000.0, "positions": {}},
        ],
    }
    with pytest.raises(ValueError, match="strict_cagr"):
        r46_verifier._strict_recompute_curve(inner)


def test_strict_curve_does_not_accept_holdings_history_as_metric_authority() -> None:
    """A rehashed holdings-history row must not choose replay metrics."""

    inner: dict[str, Any] = {
        "result_metrics": {
            "starting_equity": 100_000.0,
            "ending_equity": 101_000.0,
            "total_return": 0.01,
            "gross_return": 0.01,
            "net_return": 0.01,
            "max_drawdown": 0.0,
            "cagr": 1.01**365 - 1.0,
            "annualized_volatility": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "turnover": 0.0,
            "fee_total": 0.0,
            "slippage_cost_total": 0.0,
            "spread_cost_total": 0.0,
            "dividend_total": 0.0,
            "average_holdings_count": 8.0,
            "average_cash_ratio": 0.25,
            "maximum_concentration": 0.5,
            "fill_count": 0,
            "rebalance_count": 0,
            "closed_positions": 0,
            "duration_days": 1,
            "benchmark_return": 0.01,
            "excess_return": 0.0,
        },
        "equity_curve": [100_000.0, 101_000.0],
        "equity_curve_timestamps": [TS.isoformat(), TS2.isoformat()],
        "benchmark_curve": [100.0, 101.0],
        "benchmark_curve_timestamps": [TS.isoformat(), TS2.isoformat()],
        "fills": [],
        "rebalances": [],
        "dividend_events": [],
        "snapshots": [
            {"timestamp": TS.isoformat(), "cash": 100_000.0, "equity": 100_000.0, "positions": {}},
            {"timestamp": TS2.isoformat(), "cash": 101_000.0, "equity": 101_000.0, "positions": {}},
        ],
        "holdings_history": [
            {"timestamp": TS.isoformat(), "holdings_count": 7, "cash_ratio": 0.25, "maximum_concentration": 0.5},
            {"timestamp": TS2.isoformat(), "holdings_count": 9, "cash_ratio": 0.25, "maximum_concentration": 0.5},
        ],
    }
    with pytest.raises(ValueError, match="strict_holdings_history_untrusted"):
        r46_verifier._strict_recompute_curve(inner)


def _strict_replay_unit_inputs(
    *, qty: float, terminal_applied: bool = False, liquidated_symbols: list[str] | None = None
) -> tuple[dict[str, Any], dict[tuple[str, datetime], Bar], dict[str, Any], Any]:
    source_before = Bar(
        timestamp=TS - timedelta(days=1),
        symbol="TLT",
        timeframe="1d",
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        volume=10.0,
        market=Market.EQUITY,
    )
    source_fill = replace(source_before, timestamp=TS2)
    fill = {
        "timestamp": source_fill.timestamp.isoformat(),
        "symbol": "TLT",
        "side": "BUY",
        "qty": qty,
        "price": 100.0,
        "fee": 0.0,
        "owner": "r46",
        "decision_timestamp": TS.isoformat(),
        "client_order_id": f"r46|{TS.isoformat()}|TLT|buy|1",
        "reference_price": 100.0,
        "slippage_cost": 0.0,
        "spread_cost": 0.0,
        "notional": qty * 100.0,
        "status": "FILLED",
        "source_bar_key": f"TLT|{source_fill.timestamp.isoformat()}",
        "source_bar_row_sha256": r46_verifier._bar_source_row_hash(source_fill),
    }
    terminal = {
        "applied": terminal_applied,
        "liquidated_symbols": list(liquidated_symbols or []),
        "cash_before": 100_000.0 - qty * 100.0,
        "cash_after": 100_000.0 - qty * 100.0,
        "cash_delta": 0.0,
        "fee": 0.0,
        "slippage_cost": 0.0,
        "spread_cost": 0.0,
    }
    inner: dict[str, Any] = {
        "initial_cash": 100_000.0,
        "cash": 100_000.0 - qty * 100.0,
        "fees": 0.0,
        "slippage_cost_total": 0.0,
        "spread_cost_total": 0.0,
        "dividend_total": 0.0,
        "audit_context": {
            "execution_max_participation_pct": 0.10,
            "liquidity_lookback_bars": 20,
            "terminal_policy": "T1",
        },
        "event_schedules": [{"entry_decision": TS.isoformat()}],
        "fills": [fill],
        "dividend_events": [],
        "terminal_liquidation": terminal,
        "terminal_audit": [],
    }
    bars = {
        (str(source_before.symbol), source_before.timestamp): source_before,
        (str(source_fill.symbol), source_fill.timestamp): source_fill,
    }
    contract = {
        "slippage_bps": 0.0,
        "spread_bps": 0.0,
        "commission": 0.0,
        "crypto_taker_fee_bps": 0.0,
        "equity_taf_per_share": 0.0,
        "equity_taf_cap": 0.0,
        "equity_cat_per_share": 0.0,
        "equity_sec_fee_rate": 0.0,
        "equity_use_effective_dated_fees": False,
        "equity_fee_multiplier": 1.0,
    }
    incumbent = type("UnitIncumbent", (), {"terminal_map": {}})()
    return inner, bars, contract, incumbent


def test_file_backed_terminal_owner_flip_is_rejected(tmp_path: Path) -> None:
    inner, bars, contract, incumbent = _strict_replay_unit_inputs(qty=0.5)
    timestamp = TS2
    positions_before = {
        "TLT": {
            "qty": 0.5,
            "avg_price": 100.0,
            "owner": "r46",
            "entry_fee": 0.0,
            "realized_pnl_accumulator": 0.0,
        }
    }
    terminal_identity = f"TLT|{timestamp.isoformat()}|FINAL_BAR"
    terminal_hash = hashlib.sha256(
        r46_verifier._canonical_bytes(
            {"entity_id": "TLT", "last_bar": timestamp.isoformat(), "effective_policy": "FINAL_BAR"}
        )
    ).hexdigest()
    inner["terminal_liquidation"] = {
        "applied": True,
        "liquidated_symbols": ["TLT"],
        "cash_before": 99_950.0,
        "cash_after": 100_000.0,
        "cash_delta": 50.0,
        "fee": 0.0,
        "slippage_cost": 0.0,
        "spread_cost": 0.0,
    }
    inner["cash"] = 100_000.0
    inner["terminal_audit"] = [
        {
            "symbol": "TLT",
            "owner": "r46",
            "last_bar": timestamp.isoformat(),
            "last_close": 100.0,
            "qty": 0.5,
            "effective_policy": "FINAL_BAR",
            "policy": "FINAL_BAR",
            "terminal_map_identity": terminal_identity,
            "terminal_map_hash": terminal_hash,
            "avg_price": 100.0,
            "entry_fee": 0.0,
            "realized_pnl": 0.0,
            "fill_price": 100.0,
            "fee": 0.0,
            "slippage_cost": 0.0,
            "spread_cost": 0.0,
            "proceeds": 50.0,
            "cash_before": 99_950.0,
            "cash_after": 100_000.0,
            "position_removed": True,
            "positions_before": positions_before,
            "positions_after": {},
            "pre_equity": 100_000.0,
            "post_equity": 100_000.0,
        }
    ]
    inner["equity_curve"] = [100_000.0]
    inner["equity_curve_timestamps"] = [timestamp.isoformat()]
    inner["snapshots"] = [
        {
            "timestamp": timestamp.isoformat(),
            "cash": 100_000.0,
            "equity": 100_000.0,
            "fees": 0.0,
            "positions": {},
            "realized_holdings": {},
            "gross_exposure": 0.0,
            "cash_weight": 1.0,
        }
    ]
    inner["positions"] = {}
    inner["realized_holdings"] = {}
    payload_path = tmp_path / "terminal-owner-flip.json"
    payload_path.write_text(json.dumps(inner, sort_keys=True), encoding="utf-8")
    persisted = json.loads(payload_path.read_text(encoding="utf-8"))
    persisted["terminal_audit"][0]["owner"] = "incumbent_v3"
    persisted["ledger_sha256"] = r46_runner._canonical_hash(persisted, "ledger_sha256")
    payload_path.write_text(json.dumps(persisted, sort_keys=True), encoding="utf-8")
    with pytest.raises(ValueError, match="strict_replay_terminal_owner"):
        r46_verifier._strict_replay_inner(
            inner=json.loads(payload_path.read_text(encoding="utf-8")),
            bars=bars,
            contract=contract,
            dividend_rows_by_key={},
            incumbent=incumbent,
        )


def test_strict_replay_rejects_aggregate_fragments_over_source_bar_cap() -> None:
    inner, bars, contract, incumbent = _strict_replay_unit_inputs(qty=1.1)
    # Split fragments are intentionally on the same source bar in this unit
    # fixture; the replay's aggregate check must apply to the grouped quantity.
    first = dict(inner["fills"][0])
    second = dict(first)
    first["qty"] = 0.6
    first["notional"] = 60.0
    second["qty"] = 0.5
    second["notional"] = 50.0
    second["client_order_id"] = "r46|fragment-2"
    inner["fills"] = [first, second]
    with pytest.raises(ValueError, match="strict_replay_aggregate_participation"):
        r46_verifier._strict_replay_inner(
            inner=inner,
            contract=contract,
            bars=bars,
            dividend_rows_by_key={},
            incumbent=incumbent,
        )


def test_strict_replay_rejects_equal_timestamp_fragments_for_one_order() -> None:
    inner, bars, contract, incumbent = _strict_replay_unit_inputs(qty=0.1)
    second = dict(inner["fills"][0])
    second["qty"] = 0.1
    second["notional"] = 10.0
    inner["fills"] = [inner["fills"][0], second]
    with pytest.raises(ValueError, match="strict_replay_fill_order"):
        r46_verifier._strict_replay_inner(
            inner=inner,
            contract=contract,
            bars=bars,
            dividend_rows_by_key={},
            incumbent=incumbent,
        )


def test_native_structural_rejects_equal_timestamp_fragments_for_one_order() -> None:
    payload = copy.deepcopy(native_adapter_smoke()["ledger"])
    first = copy.deepcopy(payload["fills"][0])
    payload["fills"].insert(1, first)  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="fill_timestamp_order"):
        r46_verifier._verify_native_structural(payload)


def test_strict_replay_adv_cutoff_advances_for_two_bar_partial_order() -> None:
    inner, bars, contract, incumbent = _strict_replay_unit_inputs(qty=1.0)
    first = dict(inner["fills"][0])
    first["qty"] = 1.0
    first["notional"] = 100.0
    first_bar = bars[("TLT", TS2)]
    bars[("TLT", TS2)] = replace(first_bar, volume=1_000.0)
    first["source_bar_row_sha256"] = r46_verifier._bar_source_row_hash(bars[("TLT", TS2)])
    third_timestamp = TS2 + timedelta(days=1)
    third_bar = replace(first_bar, timestamp=third_timestamp, volume=1_000.0)
    bars[("TLT", third_timestamp)] = third_bar
    second = dict(first)
    second["timestamp"] = third_timestamp.isoformat()
    second["qty"] = 10.0
    second["notional"] = 1_000.0
    second["source_bar_key"] = f"TLT|{third_timestamp.isoformat()}"
    second["source_bar_row_sha256"] = r46_verifier._bar_source_row_hash(third_bar)
    inner["fills"] = [first, second]
    with pytest.raises(ValueError, match="strict_replay_terminal_incomplete"):
        r46_verifier._strict_replay_inner(
            inner=inner,
            contract=contract,
            bars=bars,
            dividend_rows_by_key={},
            incumbent=incumbent,
        )


def test_strict_replay_rejects_false_terminal_with_final_holdings() -> None:
    inner, bars, contract, incumbent = _strict_replay_unit_inputs(qty=0.5)
    with pytest.raises(ValueError, match="strict_replay_terminal_incomplete"):
        r46_verifier._strict_replay_inner(
            inner=inner,
            contract=contract,
            bars=bars,
            dividend_rows_by_key={},
            incumbent=incumbent,
        )


def test_strict_replay_rejects_duplicate_terminal_symbol_declaration() -> None:
    inner, bars, contract, incumbent = _strict_replay_unit_inputs(
        qty=0.0, liquidated_symbols=["TLT", "TLT"]
    )
    inner["fills"] = []
    inner["cash"] = 100_000.0
    with pytest.raises(ValueError, match="strict_replay_terminal_symbols"):
        r46_verifier._strict_replay_inner(
            inner=inner,
            contract=contract,
            bars=bars,
            dividend_rows_by_key={},
            incumbent=incumbent,
        )


def test_strict_curve_binds_benchmark_to_pinned_spy_bars() -> None:
    inner: dict[str, Any] = {
        "result_metrics": {
            "starting_equity": 100_000.0,
            "ending_equity": 101_000.0,
            "total_return": 0.01,
            "gross_return": 0.01,
            "net_return": 0.01,
            "max_drawdown": 0.0,
            "cagr": 1.01**365 - 1.0,
            "annualized_volatility": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "turnover": 0.0,
            "fee_total": 0.0,
            "slippage_cost_total": 0.0,
            "spread_cost_total": 0.0,
            "dividend_total": 0.0,
            "average_holdings_count": 0.0,
            "average_cash_ratio": 1.0,
            "maximum_concentration": 0.0,
            "fill_count": 0,
            "rebalance_count": 0,
            "closed_positions": 0,
            "duration_days": 1,
            "benchmark_return": 0.01,
            "excess_return": 0.0,
        },
        "equity_curve": [100_000.0, 101_000.0],
        "equity_curve_timestamps": [TS.isoformat(), TS2.isoformat()],
        "benchmark_curve": [100.0, 101.0],
        "benchmark_curve_timestamps": [TS.isoformat(), TS2.isoformat()],
        "fills": [],
        "rebalances": [],
        "dividend_events": [],
        "snapshots": [
            {"timestamp": TS.isoformat(), "cash": 100_000.0, "equity": 100_000.0, "positions": {}},
            {"timestamp": TS2.isoformat(), "cash": 101_000.0, "equity": 101_000.0, "positions": {}},
        ],
    }
    expected = (
        Bar(TS, "SPY", "1d", 100.0, 100.0, 100.0, 100.0, 1_000_000.0, Market.EQUITY),
        Bar(TS2, "SPY", "1d", 101.0, 101.0, 101.0, 101.0, 1_000_000.0, Market.EQUITY),
    )
    r46_verifier._strict_recompute_curve(inner, expected_benchmark_bars=expected)
    inner["benchmark_curve"][1] = 102.0  # type: ignore[index]
    with pytest.raises(ValueError, match="strict_benchmark_source_binding"):
        r46_verifier._strict_recompute_curve(inner, expected_benchmark_bars=expected)


def test_strict_git_lineage_requires_base_source_freeze_ancestry_and_head(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "r46@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "R46"], cwd=tmp_path, check=True)
    tracked = tmp_path / "tracked.py"
    tracked.write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    tracked.write_text("v2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "source"], cwd=tmp_path, check=True)
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    tracked.write_text("v3\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "freeze"], cwd=tmp_path, check=True)
    freeze = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()

    r46_verifier._strict_git_lineage(tmp_path, base=base, source=source, freeze=freeze)
    with pytest.raises(ValueError, match="strict_freeze_commit_not_head"):
        r46_verifier._strict_git_lineage(tmp_path, base=base, source=source, freeze=source)


def test_strict_contained_file_rejects_symlink_sidecars(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    sidecar = tmp_path / "sidecar.json"
    sidecar.symlink_to(target)
    with pytest.raises(ValueError, match="strict_sidecar_symlink"):
        r46_verifier._strict_contained_file(tmp_path, "sidecar.json", "sidecar")


def test_attestation_write_is_no_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload, _ = _valid_native_attestation_fixture(tmp_path, monkeypatch)
    path = tmp_path / "attestation.json"
    write_attestation(path, payload=payload)
    with pytest.raises(FileExistsError):
        write_attestation(path, payload=payload)


def test_package_backed_runner_exposes_real_engine_and_exact_package_entrypoints() -> None:
    """The production surface must be package-backed, not the old synthetic CLI."""

    assert callable(getattr(r46_runner, "load_r46_v2_package", None))
    assert callable(getattr(r46_runner, "run_package_backed", None))


def _strict_v5_source_file_overrides() -> dict[str, Any]:
    """Keep disposable governance fixtures cross-linked to code-owned V5 paths."""

    source_files = {
        preflight.V5_INCUMBENT_PATHS[key]: {"path": preflight.V5_INCUMBENT_PATHS[key]}
        for key in ("lifetimes", "seam")
    }
    return {"clean_tree_observed": True, "source_files": source_files}


def test_governance_package_native_preflight_and_runner_gate_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Traverse the real governance/package/native/preflight gate without economics."""

    from scripts.research import build_r46_nyse_calendar_2026_09_10 as calendar_builder
    from scripts.research import build_r46_v2_data_package_2026_09_10 as package_builder
    from scripts.research import build_r46_v2_governance_2026_09_10 as governance_builder

    root = tmp_path / "fixture-repo"
    root.mkdir()
    v1 = json.loads(
        (REPO / "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.json").read_text(
            encoding="utf-8"
        )
    )
    copy_paths = set(v1["source_provenance"]["source_files"])
    for spec in v1["source_provenance"].values():
        if isinstance(spec, dict) and isinstance(spec.get("path"), str):
            copy_paths.add(spec["path"])
    copy_paths.update(
        {
            "scripts/research/acquire_r46_tlt_market_data_2026_09_10.py",
            "scripts/research/acquire_r46_treasury_documents_2026_09_10.py",
            "scripts/research/acquire_r46_treasury_xml_v2_2026_09_10.py",
            "scripts/research/build_r46_nyse_calendar_2026_09_10.py",
            "scripts/research/build_r46_v2_data_package_2026_09_10.py",
            "scripts/research/build_r46_v2_governance_2026_09_10.py",
            "scripts/research/build_r46_publication_availability_evidence_2026_09_11.py",
            "scripts/research/r46_v2_preflight_2026_09_10.py",
            "scripts/research/r46_v3_oracle_2026_09_11.py",
            "scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py",
            "scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py",
            "scripts/research/build_r46_native_ledger_attestation_2026_09_10.py",
            "scripts/research/r46_treasury_auction_readiness_2026_09_10.py",
            "tests/research/test_r46_v2_execution_2026_09_10.py",
            "tests/research/test_r46_v2_data_package_2026_09_10.py",
            "tests/research/test_r46_v2_governance_2026_09_10.py",
            "tests/research/test_r46_tlt_market_data_2026_09_10.py",
            "tests/research/test_r46_treasury_xml_v2_2026_09_10.py",
            "tests/research/test_r46_nyse_calendar_2026_09_10.py",
            "tests/research/test_r46_treasury_acquisition_2026_09_10.py",
            "src/parayabuck/core/models.py",
            "src/parayabuck/data/market_dataset.py",
            "src/parayabuck/portfolio/__init__.py",
            "src/parayabuck/portfolio/base.py",
            "src/parayabuck/portfolio/models.py",
            "src/parayabuck/portfolio/equity_residual_momentum.py",
            "src/parayabuck/portfolio/equity_residual_momentum_v3.py",
            "src/parayabuck/backtest/terminal_aware_engine.py",
            "src/parayabuck/backtest/regulatory_fees.py",
            "scripts/run_symmetric_v5_validation.py",
            "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.json",
            "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.schema.json",
            "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_source_matrix.json",
            "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_holdout_custody_contract.json",
            "data/research_receipts/r46_treasury_public_source_evidence_2026_09_10_v1.json",
            "data/research_receipts/r46_treasury_publication_availability_evidence_2026_09_11_inventory_raw_hash_v3.json",
            "uv.lock",
        }
    )
    for relative in copy_paths:
        source = REPO / relative
        if not source.is_file():
            continue
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    final_treasury_run = Path(
        "data/research/runs/r46_treasury_xml_v2_2026_09_11_inventory_raw_hash_2026_09_11"
    )
    shutil.copytree(REPO / final_treasury_run, root / final_treasury_run)
    tlt_market_run = Path("data/research/runs/r46_tlt_market_data_v2_effective_open_raw_source_2026_09_11")
    shutil.copytree(REPO / tlt_market_run, root / tlt_market_run)
    calendar_run = Path("data/research/runs/r46_calendar_v2_2026_09_10")
    shutil.copytree(REPO / calendar_run, root / calendar_run)

    # Rebind the copied calendar receipt to the fixture's current lockfile.
    # The production receipt intentionally preserves the lock hash observed at
    # acquisition time, while this synthetic git fixture copies today's lock.
    fixture_calendar_receipt = root / calendar_run / "nyse_calendar.receipt.json"
    calendar_source = json.loads(fixture_calendar_receipt.read_text(encoding="utf-8"))
    calendar_dependency = calendar_builder.read_dependency_pin(
        root / "uv.lock", installed_version=calendar_builder.DEPENDENCY_VERSION
    )
    calendar_source["provenance"]["dependency"] = calendar_dependency
    calendar_source["provenance"]["uv_lock_path"] = calendar_dependency["lock"]["path"]
    calendar_source["provenance"]["uv_lock_sha256"] = calendar_dependency["lock"]["sha256"]
    calendar_source["integrity"]["receipt_sha256"] = _canonical(
        calendar_source, "receipt_sha256"
    )
    fixture_calendar_receipt.write_text(
        json.dumps(calendar_source, sort_keys=True) + "\n", encoding="utf-8"
    )

    fixture_treasury_documents = root / final_treasury_run / "documents.jsonl"

    # The production Treasury receipt was written from the source checkout and
    # therefore carries one absolute inventory path. Rebind only that path to
    # the copied, tracked public receipt inside this fixture and recompute the
    # fixture receipt self-hash. The document/XML bytes and receipt semantics
    # stay identical to the real ignored run.
    public_source_receipt = Path(
        "data/research_receipts/r46_treasury_public_source_evidence_2026_09_10_v1.json"
    )
    fixture_treasury_receipt = root / final_treasury_run / "receipt.json"
    treasury_source = json.loads(fixture_treasury_receipt.read_text(encoding="utf-8"))
    treasury_source["inventory"]["path"] = public_source_receipt.as_posix()  # type: ignore[index]
    treasury_source["integrity"]["receipt_sha256"] = _canonical(  # type: ignore[index]
        treasury_source, "receipt_sha256"
    )
    fixture_treasury_receipt.write_text(
        json.dumps(treasury_source, sort_keys=True) + "\n", encoding="utf-8"
    )

    fixture_publication_evidence = Path(
        "data/research_receipts/r46_treasury_publication_availability_evidence_fixture_2026_09_11_v2.json"
    )
    evidence_source = json.loads(
        (
            root
            / "data/research_receipts/r46_treasury_publication_availability_evidence_2026_09_11_inventory_raw_hash_v3.json"
        ).read_text(encoding="utf-8")
    )
    evidence_source["sources"]["receipt"]["path"] = (  # type: ignore[index]
        final_treasury_run / "receipt.json"
    ).as_posix()
    evidence_source["sources"]["receipt"]["raw_sha256"] = hashlib.sha256(  # type: ignore[index]
        fixture_treasury_receipt.read_bytes()
    ).hexdigest()
    evidence_source["sources"]["receipt"]["byte_size"] = fixture_treasury_receipt.stat().st_size  # type: ignore[index]
    evidence_source["integrity"]["evidence_sha256"] = _canonical(  # type: ignore[index]
        evidence_source, "evidence_sha256"
    )
    fixture_publication_evidence_path = root / fixture_publication_evidence
    fixture_publication_evidence_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_publication_evidence_path.write_text(
        json.dumps(evidence_source, sort_keys=True) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        governance_builder,
        "PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE",
        fixture_publication_evidence,
    )
    monkeypatch.setattr(
        governance_builder,
        "PUBLICATION_AVAILABILITY_EVIDENCE_RAW_SHA256",
        hashlib.sha256(fixture_publication_evidence_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        governance_builder,
        "PUBLICATION_AVAILABILITY_EVIDENCE_BYTE_SIZE",
        fixture_publication_evidence_path.stat().st_size,
    )
    monkeypatch.setattr(preflight, "PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE", fixture_publication_evidence)
    monkeypatch.setattr(
        preflight,
        "PUBLICATION_AVAILABILITY_EVIDENCE_RAW_SHA256",
        hashlib.sha256(fixture_publication_evidence_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(preflight, "PUBLICATION_AVAILABILITY_EVIDENCE_BYTE_SIZE", fixture_publication_evidence_path.stat().st_size)
    fixture_treasury_receipt_sha256 = hashlib.sha256(fixture_treasury_receipt.read_bytes()).hexdigest()
    fixture_treasury_documents_sha256 = hashlib.sha256(fixture_treasury_documents.read_bytes()).hexdigest()
    monkeypatch.setattr(
        governance_builder,
        "FINAL_TREASURY_RECEIPT_RAW_SHA256",
        fixture_treasury_receipt_sha256,
    )
    monkeypatch.setattr(
        governance_builder,
        "FINAL_TREASURY_RECEIPT_BYTE_SIZE",
        fixture_treasury_receipt.stat().st_size,
    )
    monkeypatch.setattr(
        governance_builder,
        "FINAL_TREASURY_DOCUMENTS_RAW_SHA256",
        fixture_treasury_documents_sha256,
    )
    monkeypatch.setattr(
        governance_builder,
        "FINAL_TREASURY_DOCUMENTS_BYTE_SIZE",
        fixture_treasury_documents.stat().st_size,
    )
    monkeypatch.setattr(preflight, "FINAL_TREASURY_RECEIPT_RAW_SHA256", fixture_treasury_receipt_sha256)
    monkeypatch.setattr(preflight, "FINAL_TREASURY_RECEIPT_BYTE_SIZE", fixture_treasury_receipt.stat().st_size)
    monkeypatch.setattr(preflight, "FINAL_TREASURY_DOCUMENTS_RAW_SHA256", fixture_treasury_documents_sha256)
    monkeypatch.setattr(preflight, "FINAL_TREASURY_DOCUMENTS_BYTE_SIZE", fixture_treasury_documents.stat().st_size)

    # Minimal provenance-only V5 fixture: the package/economic fixture uses a
    # synthetic incumbent, but preflight must still exercise the exact
    # bars/lifetimes/corporate-actions manifest cross-links and cutoff gate.
    historical = root / "data/historical"
    historical.mkdir(parents=True, exist_ok=True)
    bars_v5 = historical / "equity_pit_symmetric_2016_2026_v5.csv"
    lifetimes_v5 = historical / "equity_entity_lifetimes_full_v5.csv"
    actions_v5 = historical / "equity_survivorship_full_corporate_actions_v5.csv"
    bars_v5.write_text("header\n", encoding="utf-8")
    lifetimes_v5.write_text("header\n", encoding="utf-8")
    actions_v5.write_text("header\n", encoding="utf-8")
    bars_manifest = {
        "dataset": str(bars_v5),
        "sha256": hashlib.sha256(bars_v5.read_bytes()).hexdigest(),
        "byte_size": bars_v5.stat().st_size,
        "lifetime_table": str(lifetimes_v5),
        "lifetime_table_hash": hashlib.sha256(lifetimes_v5.read_bytes()).hexdigest(),
        "last_date": "2026-07-31T04:00:00Z",
    }
    actions_manifest = {
        "dataset": str(actions_v5),
        "sha256": hashlib.sha256(actions_v5.read_bytes()).hexdigest(),
        "byte_size": actions_v5.stat().st_size,
    }
    (historical / "equity_pit_symmetric_2016_2026_v5.manifest.json").write_text(
        json.dumps(bars_manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    (historical / "equity_survivorship_full_corporate_actions_v5.manifest.json").write_text(
        json.dumps(actions_manifest, sort_keys=True) + "\n", encoding="utf-8"
    )

    init = subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True, text=True, check=False)
    assert init.returncode == 0, init.stderr
    subprocess.run(["git", "config", "user.email", "r46-test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "R46 fixture"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "R46 C_src fixture"], cwd=root, check=True)
    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()

    manifest_paths = governance_builder.build_v2_bundle(
        repo_root=root,
        output_root=root / "data/research_manifests",
        source_commit=source_commit,
        base_commit=source_commit,
        source_overrides=_strict_v5_source_file_overrides(),
    )
    manifest_path = manifest_paths["manifest"]

    source_documents = root / final_treasury_run / "documents.jsonl"
    source_treasury_receipt = root / final_treasury_run / "receipt.json"
    source_tlt_run = root / tlt_market_run
    source_calendar_run = root / calendar_run
    source_market_receipt = json.loads(
        (source_tlt_run / "receipt.json").read_text(encoding="utf-8")
    )
    source_calendar_receipt = json.loads(
        (source_calendar_run / "nyse_calendar.receipt.json").read_text(encoding="utf-8")
    )
    source_document_rows = [
        json.loads(line) for line in source_documents.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    source_documents_available_at = max(
        datetime.fromisoformat(str(row["available_at"]).replace("Z", "+00:00"))
        for row in source_document_rows
    ).astimezone(UTC).isoformat().replace("+00:00", "Z")
    source_artifacts = source_market_receipt["artifacts"]
    source_metadata = {
        "documents": {
            "source_vintage": "treasurydirect-official-xml-r46-policy-final-2026-09-11",
            "available_at": source_documents_available_at,
        },
        "bars": {
            "source_vintage": source_artifacts["split_bars"]["source_vintage"],
            "available_at": source_artifacts["split_bars"]["available_at"],
        },
        "dividends": {
            "source_vintage": source_artifacts["dividends"]["source_vintage"],
            "available_at": source_artifacts["dividends"]["available_at"],
        },
        "eligibility": {
            "source_vintage": source_artifacts["eligibility"]["source_vintage"],
            "available_at": source_artifacts["eligibility"]["available_at"],
        },
        "calendar": {
            "source_vintage": "pandas-market-calendars-5.4.0",
            "available_at": source_calendar_receipt["available_at"],
        },
    }

    governance = json.loads(manifest_path.read_text(encoding="utf-8"))
    publication_policy = governance["publication_availability_policy"]
    event_ids = list(publication_policy["expected_event_ids"])
    assert len(event_ids) == 127
    missing_release_ids = list(publication_policy["missing_release"]["expected_event_ids"])
    assert len(missing_release_ids) == 15
    assert missing_release_ids == sorted(missing_release_ids)
    package_path = root / "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_data_package.json"
    package_builder.build_r46_v2_data_package(
        manifest_path,
        documents=source_documents,
        bars=source_tlt_run / "split_bars.csv",
        dividends=source_tlt_run / "dividends.csv",
        eligibility=source_tlt_run / "eligibility.jsonl",
        calendar=source_calendar_run / "nyse_calendar.jsonl",
        acquisition_receipt=source_tlt_run / "receipt.json",
        treasury_receipt=source_treasury_receipt,
        calendar_receipt=source_calendar_run / "nyse_calendar.receipt.json",
        artifact_metadata=source_metadata,
        expected_event_count=len(event_ids),
        expected_event_ids=tuple(event_ids),
        coverage={
            "event_episodes": 127,
            "holding_intervals": 635,
            "missing_rate": 0.0,
            "fifth_open_exit": 127,
        },
        output=package_path,
        repo_root=root,
    )

    monkeypatch.setattr(native_builder, "REPO", root)
    monkeypatch.setattr(native_builder, "DEFAULT_GOVERNANCE", manifest_path)
    monkeypatch.setattr(native_builder, "DEFAULT_PACKAGE", package_path)
    monkeypatch.setattr(native_builder, "RUNNER", "scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py")
    monkeypatch.setattr(
        native_builder, "VERIFIER", "scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py"
    )
    monkeypatch.setattr(native_builder, "SOURCE", "src/parayabuck/backtest/portfolio_engine.py")
    attestation = native_builder.build_attestation()
    native_path = root / "data/research_receipts/r46_treasury_auction_unwind_tlt_v2_2026_09_10_native_attestation.json"
    native_path.parent.mkdir(parents=True, exist_ok=True)
    write_attestation(native_path, payload=attestation)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    review: dict[str, object] = {
        "schema_version": "r46-independent-software-review-v1",
        "status": "VALIDATED",
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "reviewed_commit": source_commit,
        "governance_manifest_sha256": manifest["integrity"]["manifest_sha256"],
        "independence": "independent_software_review",
        "reviewed_paths": ["scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py"],
        "integrity": {"review_sha256": ""},
    }
    review["integrity"]["review_sha256"] = _canonical(review, "review_sha256")  # type: ignore[index]
    review_path = root / "data/research_receipts/r46_v2_independent_software_review_2026_09_10.json"
    review_path.write_text(json.dumps(review, sort_keys=True) + "\n", encoding="utf-8")

    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "R46 freeze evidence fixture"], cwd=root, check=True)
    freeze_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()

    result = preflight.verify_v2_manifest(manifest_path, repo_root=root, freeze_commit=freeze_commit)
    assert result["status"] == "PRE_RUN_OK", result["blockers"]
    gate_path = root / "data/research_receipts/r46_v2_preflight_receipt.json"
    preflight.persist_preflight_receipt(result, gate_path)
    persisted = json.loads(gate_path.read_text(encoding="utf-8"))
    assert persisted == result["preflight_receipt"]

    monkeypatch.setattr(r46_runner, "REPO", root)
    assert r46_runner._validate_pre_run_receipt(gate_path, repo_root=root)["status"] == "PRE_RUN_OK"
    debug_recheck = preflight.verify_v2_manifest(
        manifest_path,
        data_package_manifest_path=package_path,
        freeze_commit=freeze_commit,
        repo_root=root,
        preflight_receipt_path=gate_path,
    )
    assert debug_recheck["status"] == "PRE_RUN_OK", debug_recheck["blockers"]
    verified = r46_runner._verify_persisted_preflight(
        manifest_path=manifest_path,
        package_path=package_path,
        receipt=persisted,
        repo_root=root,
        preflight_receipt_path=gate_path,
    )
    assert verified["status"] == "PRE_RUN_OK"
    assert verified["preflight_receipt"] == persisted


def _copy_strict_fixture_source_tree(root: Path) -> None:
    """Copy only the R46 source/provenance closure into a disposable repo."""

    v1_path = REPO / "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.json"
    v1 = json.loads(v1_path.read_text(encoding="utf-8"))
    copy_paths = set(v1["source_provenance"]["source_files"])
    for spec in v1["source_provenance"].values():
        if isinstance(spec, dict) and isinstance(spec.get("path"), str):
            copy_paths.add(spec["path"])
    copy_paths.update(
        {
            "scripts/research/acquire_r46_tlt_market_data_2026_09_10.py",
            "scripts/research/acquire_r46_treasury_documents_2026_09_10.py",
            "scripts/research/acquire_r46_treasury_xml_v2_2026_09_10.py",
            "scripts/research/build_r46_nyse_calendar_2026_09_10.py",
            "scripts/research/build_r46_v2_data_package_2026_09_10.py",
            "scripts/research/build_r46_v2_governance_2026_09_10.py",
            "scripts/research/build_r46_publication_availability_evidence_2026_09_11.py",
            "scripts/research/r46_v2_preflight_2026_09_10.py",
            "scripts/research/r46_v3_oracle_2026_09_11.py",
            "scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py",
            "scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py",
            "scripts/research/build_r46_native_ledger_attestation_2026_09_10.py",
            "scripts/research/r46_treasury_auction_readiness_2026_09_10.py",
            "tests/research/test_r46_v2_execution_2026_09_10.py",
            "tests/research/test_r46_v2_data_package_2026_09_10.py",
            "tests/research/test_r46_v2_governance_2026_09_10.py",
            "tests/research/test_r46_tlt_market_data_2026_09_10.py",
            "tests/research/test_r46_treasury_xml_v2_2026_09_10.py",
            "tests/research/test_r46_nyse_calendar_2026_09_10.py",
            "tests/research/test_r46_treasury_acquisition_2026_09_10.py",
            "src/parayabuck/core/models.py",
            "src/parayabuck/data/market_dataset.py",
            "src/parayabuck/portfolio/__init__.py",
            "src/parayabuck/portfolio/base.py",
            "src/parayabuck/portfolio/models.py",
            "src/parayabuck/portfolio/equity_residual_momentum.py",
            "src/parayabuck/portfolio/equity_residual_momentum_v3.py",
            "src/parayabuck/backtest/terminal_aware_engine.py",
            "src/parayabuck/backtest/regulatory_fees.py",
            "scripts/run_symmetric_v5_validation.py",
            "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.json",
            "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10.schema.json",
            "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_source_matrix.json",
            "data/research_manifests/r46_treasury_auction_unwind_tlt_v1_2026_09_10_holdout_custody_contract.json",
            "data/research_receipts/r46_treasury_public_source_evidence_2026_09_10_v1.json",
            "data/research_receipts/r46_treasury_publication_availability_evidence_2026_09_11_inventory_raw_hash_v3.json",
            "config/settings.yaml",
            "uv.lock",
        }
    )
    for relative in sorted(copy_paths):
        source = REPO / relative
        if not source.is_file():
            continue
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    for relative in (
        "data/research/runs/r46_treasury_xml_v2_2026_09_11_inventory_raw_hash_2026_09_11",
        "data/research/runs/r46_tlt_market_data_v2_effective_open_raw_source_2026_09_11",
        "data/research/runs/r46_calendar_v2_2026_09_10",
    ):
        shutil.copytree(REPO / relative, root / relative)


def _write_small_valid_v5_fixture(root: Path, tlt_bars_path: Path) -> None:
    """Write tiny, seam-valid incumbent inputs covering package timestamps."""

    historical = root / "data/historical"
    historical.mkdir(parents=True, exist_ok=True)
    with tlt_bars_path.open(newline="", encoding="utf-8") as handle:
        timestamps = sorted({str(row["regular_open_timestamp"]) for row in csv.DictReader(handle)})
    assert timestamps, "package fixture must have TLT bars"
    # The earliest auction event predates the incumbent strategy's warm-up
    # horizon.  Add deterministic pre-package bars so the real incumbent
    # strategy has a prior target when the first R46 event is replayed.
    first_timestamp = datetime.fromisoformat(timestamps[0].replace("Z", "+00:00"))
    warmup_timestamps: list[str] = []
    cursor = first_timestamp.date() - timedelta(days=1)
    warmup_start = cursor - timedelta(days=365 * 6)
    while warmup_start <= cursor:
        if warmup_start.weekday() < 5:
            warmup_timestamps.append(f"{warmup_start.isoformat()}T14:30:00Z")
        warmup_start += timedelta(days=1)
    timestamps = sorted(set(warmup_timestamps).union(timestamps))
    bars_path = historical / "equity_pit_symmetric_2016_2026_v5.csv"
    with bars_path.open("w", newline="", encoding="utf-8") as handle:
        fields = ["timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market"]
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        prices = {"SPY": 100.0, "AAA": 90.0, "BBB": 80.0}
        betas = {"SPY": 1.0, "AAA": 1.15, "BBB": 0.85}
        residual_cycles = {
            "AAA": (0.00024, 0.00032, 0.00028, 0.00036, 0.00030, 0.00026, 0.00034),
            "BBB": (0.00016, 0.00022, 0.00019, 0.00025, 0.00021, 0.00017, 0.00023),
        }
        for index, timestamp in enumerate(timestamps):
            market_return = 0.00042 + 0.00008 * ((index % 5) - 2)
            for symbol in ("SPY", "AAA", "BBB"):
                residual = residual_cycles[symbol][index % 7] if symbol != "SPY" else 0.0
                prices[symbol] *= 1.0 + betas[symbol] * market_return + residual
                price = prices[symbol]
                writer.writerow(
                    {
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "timeframe": "1d",
                        "open": f"{price:.8f}",
                        # Keep this disposable fixture scanner-eligible: the
                        # production PIT scanner derives ATR from true range,
                        # while V3 itself consumes only the close series.
                        "high": f"{price * 1.01:.8f}",
                        "low": f"{price * 0.99:.8f}",
                        "close": f"{price:.8f}",
                        "volume": "1000000000",
                        "market": "equity",
                    }
                )
    lifetimes_path = historical / "equity_entity_lifetimes_full_v5.csv"
    with lifetimes_path.open("w", newline="", encoding="utf-8") as handle:
        fields = ["canonical_symbol", "entity_id", "first_observed_bar", "last_observed_bar", "current_status"]
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for index, symbol in enumerate(("SPY", "AAA", "BBB")):
            writer.writerow(
                {
                    "canonical_symbol": symbol,
                    "entity_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"r46-v5-{symbol}-{index}")),
                    "first_observed_bar": timestamps[0],
                    "last_observed_bar": timestamps[-1],
                    "current_status": "active",
                }
            )
    actions_path = historical / "equity_survivorship_full_corporate_actions_v5.csv"
    actions_path.write_text("raw_id,symbol,action_type,ex_date,amount\n", encoding="utf-8")
    bars_manifest = historical / "equity_pit_symmetric_2016_2026_v5.manifest.json"
    bars_manifest.write_text(
        json.dumps(
            {
                "dataset": str(bars_path),
                "sha256": hashlib.sha256(bars_path.read_bytes()).hexdigest(),
                "byte_size": bars_path.stat().st_size,
                "lifetime_table": str(lifetimes_path),
                "lifetime_table_hash": hashlib.sha256(lifetimes_path.read_bytes()).hexdigest(),
                "last_date": "2026-07-31T04:00:00Z",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    actions_manifest = historical / "equity_survivorship_full_corporate_actions_v5.manifest.json"
    actions_manifest.write_text(
        json.dumps(
            {
                "dataset": str(actions_path),
                "sha256": hashlib.sha256(actions_path.read_bytes()).hexdigest(),
                "byte_size": actions_path.stat().st_size,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _prepare_strict_green_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path | str]:
    """Build the complete persisted R46 gate in a disposable Git repository."""

    from scripts.research import build_r46_nyse_calendar_2026_09_10 as calendar_builder
    from scripts.research import build_r46_v2_data_package_2026_09_10 as package_builder
    from scripts.research import build_r46_v2_governance_2026_09_10 as governance_builder

    root = tmp_path / "strict-r46-repo"
    root.mkdir()
    _copy_strict_fixture_source_tree(root)
    (root / ".gitignore").write_text(
        "__pycache__/\n"
        "data/research_receipts/r46_v2_preflight_receipt.json\n"
        "data/research/r46_strict_fixture_outputs/\n"
        "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json.reserved.json\n"
        "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json.consumed.json\n"
        "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json.aborted_consumed.json\n",
        encoding="utf-8",
    )
    final_treasury_run = root / "data/research/runs/r46_treasury_xml_v2_2026_09_11_inventory_raw_hash_2026_09_11"
    tlt_market_run = root / "data/research/runs/r46_tlt_market_data_v2_effective_open_raw_source_2026_09_11"
    calendar_run = root / "data/research/runs/r46_calendar_v2_2026_09_10"

    # Rebind copied receipt paths and the dependency lock to the temporary root.
    fixture_calendar_receipt = calendar_run / "nyse_calendar.receipt.json"
    calendar_source = json.loads(fixture_calendar_receipt.read_text(encoding="utf-8"))
    calendar_dependency = calendar_builder.read_dependency_pin(root / "uv.lock", installed_version=calendar_builder.DEPENDENCY_VERSION)
    calendar_source["provenance"]["dependency"] = calendar_dependency
    calendar_source["provenance"]["uv_lock_path"] = calendar_dependency["lock"]["path"]
    calendar_source["provenance"]["uv_lock_sha256"] = calendar_dependency["lock"]["sha256"]
    calendar_source["integrity"]["receipt_sha256"] = _canonical(calendar_source, "receipt_sha256")
    fixture_calendar_receipt.write_text(json.dumps(calendar_source, sort_keys=True) + "\n", encoding="utf-8")

    fixture_treasury_documents = final_treasury_run / "documents.jsonl"
    public_source_receipt = Path("data/research_receipts/r46_treasury_public_source_evidence_2026_09_10_v1.json")
    fixture_treasury_receipt = final_treasury_run / "receipt.json"
    treasury_source = json.loads(fixture_treasury_receipt.read_text(encoding="utf-8"))
    treasury_source["inventory"]["path"] = public_source_receipt.as_posix()
    treasury_source["integrity"]["receipt_sha256"] = _canonical(treasury_source, "receipt_sha256")
    fixture_treasury_receipt.write_text(json.dumps(treasury_source, sort_keys=True) + "\n", encoding="utf-8")

    fixture_publication_evidence = Path(
        "data/research_receipts/r46_treasury_publication_availability_evidence_fixture_2026_09_11_v2.json"
    )
    evidence_source = json.loads(
        (root / "data/research_receipts/r46_treasury_publication_availability_evidence_2026_09_11_inventory_raw_hash_v3.json")
        .read_text(encoding="utf-8")
    )
    evidence_source["sources"]["receipt"]["path"] = (final_treasury_run / "receipt.json").relative_to(root).as_posix()
    evidence_source["sources"]["receipt"]["raw_sha256"] = hashlib.sha256(fixture_treasury_receipt.read_bytes()).hexdigest()
    evidence_source["sources"]["receipt"]["byte_size"] = fixture_treasury_receipt.stat().st_size
    evidence_source["integrity"]["evidence_sha256"] = _canonical(evidence_source, "evidence_sha256")
    evidence_path = root / fixture_publication_evidence
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence_source, sort_keys=True) + "\n", encoding="utf-8")
    for module in (governance_builder, preflight):
        monkeypatch.setattr(module, "PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE", fixture_publication_evidence)
        monkeypatch.setattr(module, "PUBLICATION_AVAILABILITY_EVIDENCE_RAW_SHA256", hashlib.sha256(evidence_path.read_bytes()).hexdigest())
        monkeypatch.setattr(module, "PUBLICATION_AVAILABILITY_EVIDENCE_BYTE_SIZE", evidence_path.stat().st_size)
        monkeypatch.setattr(module, "FINAL_TREASURY_RECEIPT_RAW_SHA256", hashlib.sha256(fixture_treasury_receipt.read_bytes()).hexdigest())
        monkeypatch.setattr(module, "FINAL_TREASURY_RECEIPT_BYTE_SIZE", fixture_treasury_receipt.stat().st_size)
        monkeypatch.setattr(module, "FINAL_TREASURY_DOCUMENTS_RAW_SHA256", hashlib.sha256(fixture_treasury_documents.read_bytes()).hexdigest())
        monkeypatch.setattr(module, "FINAL_TREASURY_DOCUMENTS_BYTE_SIZE", fixture_treasury_documents.stat().st_size)

    # Make the copied preflight module self-contained for the fresh-process
    # verifier below.  The production module pins the real evidence bytes;
    # this disposable repo deliberately has re-hashed fixture evidence.
    preflight_constants = {
        "PUBLICATION_AVAILABILITY_EVIDENCE_RAW_SHA256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "PUBLICATION_AVAILABILITY_EVIDENCE_BYTE_SIZE": evidence_path.stat().st_size,
        "FINAL_TREASURY_RECEIPT_RAW_SHA256": hashlib.sha256(fixture_treasury_receipt.read_bytes()).hexdigest(),
        "FINAL_TREASURY_RECEIPT_BYTE_SIZE": fixture_treasury_receipt.stat().st_size,
        "FINAL_TREASURY_DOCUMENTS_RAW_SHA256": hashlib.sha256(fixture_treasury_documents.read_bytes()).hexdigest(),
        "FINAL_TREASURY_DOCUMENTS_BYTE_SIZE": fixture_treasury_documents.stat().st_size,
    }
    for source_module_path in (
        root / "scripts/research/r46_v2_preflight_2026_09_10.py",
        root / "scripts/research/build_r46_v2_governance_2026_09_10.py",
    ):
        source_module_text = source_module_path.read_text(encoding="utf-8")
        source_module_text = source_module_text.replace(
            'PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE = Path(\n    "data/research_receipts/r46_treasury_publication_availability_evidence_2026_09_11_inventory_raw_hash_v3.json"\n)',
            f'PUBLICATION_AVAILABILITY_EVIDENCE_RELATIVE = Path(\n    "{fixture_publication_evidence.as_posix()}"\n)',
        )
        for name, value in preflight_constants.items():
            source_module_text = re.sub(
                rf"(?m)^{name} = .*?$",
                f"{name} = {value!r}",
                source_module_text,
            )
        source_module_path.write_text(source_module_text, encoding="utf-8")

    bars_v5 = root / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
    _write_small_valid_v5_fixture(root, tlt_market_run / "split_bars.csv")

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "r46-test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "R46 fixture"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "R46 C_src fixture"], cwd=root, check=True)
    source_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()

    manifest_paths = governance_builder.build_v2_bundle(
        repo_root=root,
        output_root=root / "data/research_manifests",
        source_commit=source_commit,
        base_commit=source_commit,
        source_overrides=_strict_v5_source_file_overrides(),
    )
    manifest_path = manifest_paths["manifest"]
    governance = json.loads(manifest_path.read_text(encoding="utf-8"))
    event_ids = tuple(governance["publication_availability_policy"]["expected_event_ids"])
    assert len(event_ids) == 127
    source_documents = final_treasury_run / "documents.jsonl"
    source_treasury_receipt = final_treasury_run / "receipt.json"
    source_tlt_receipt = tlt_market_run / "receipt.json"
    source_calendar_receipt = calendar_run / "nyse_calendar.receipt.json"
    source_market_receipt = json.loads(source_tlt_receipt.read_text(encoding="utf-8"))
    source_calendar = json.loads(source_calendar_receipt.read_text(encoding="utf-8"))
    source_document_rows = [json.loads(line) for line in source_documents.read_text(encoding="utf-8").splitlines() if line.strip()]
    source_artifacts = source_market_receipt["artifacts"]
    source_metadata = {
        "documents": {"source_vintage": "treasurydirect-official-xml-r46-policy-final-2026-09-11", "available_at": max(str(row["available_at"]) for row in source_document_rows)},
        "bars": {"source_vintage": source_artifacts["split_bars"]["source_vintage"], "available_at": source_artifacts["split_bars"]["available_at"]},
        "dividends": {"source_vintage": source_artifacts["dividends"]["source_vintage"], "available_at": source_artifacts["dividends"]["available_at"]},
        "eligibility": {"source_vintage": source_artifacts["eligibility"]["source_vintage"], "available_at": source_artifacts["eligibility"]["available_at"]},
        "calendar": {"source_vintage": "pandas-market-calendars-5.4.0", "available_at": source_calendar["available_at"]},
    }
    package_path = root / "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_data_package.json"
    package_builder.build_r46_v2_data_package(
        manifest_path,
        documents=source_documents,
        bars=tlt_market_run / "split_bars.csv",
        dividends=tlt_market_run / "dividends.csv",
        eligibility=tlt_market_run / "eligibility.jsonl",
        calendar=calendar_run / "nyse_calendar.jsonl",
        acquisition_receipt=source_tlt_receipt,
        treasury_receipt=source_treasury_receipt,
        calendar_receipt=source_calendar_receipt,
        artifact_metadata=source_metadata,
        expected_event_count=len(event_ids),
        expected_event_ids=event_ids,
        coverage={"event_episodes": 127, "holding_intervals": 635, "missing_rate": 0.0, "fifth_open_exit": 127},
        output=package_path,
        repo_root=root,
    )

    monkeypatch.setattr(native_builder, "REPO", root)
    monkeypatch.setattr(native_builder, "DEFAULT_GOVERNANCE", manifest_path)
    monkeypatch.setattr(native_builder, "DEFAULT_PACKAGE", package_path)
    monkeypatch.setattr(native_builder, "RUNNER", "scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py")
    monkeypatch.setattr(native_builder, "VERIFIER", "scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py")
    monkeypatch.setattr(native_builder, "SOURCE", "src/parayabuck/backtest/portfolio_engine.py")
    native_path = root / "data/research_receipts/r46_treasury_auction_unwind_tlt_v2_2026_09_10_native_attestation.json"
    native_path.parent.mkdir(parents=True, exist_ok=True)
    write_attestation(native_path, payload=build_attestation())
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    review = {
        "schema_version": "r46-independent-software-review-v1",
        "status": "VALIDATED",
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "reviewed_commit": source_commit,
        "governance_manifest_sha256": manifest["integrity"]["manifest_sha256"],
        "independence": "independent_software_review",
        "reviewed_paths": ["scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py"],
        "integrity": {"review_sha256": ""},
    }
    review["integrity"]["review_sha256"] = _canonical(review, "review_sha256")
    review_path = root / "data/research_receipts/r46_v2_independent_software_review_2026_09_10.json"
    review_path.write_text(json.dumps(review, sort_keys=True) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "R46 freeze evidence fixture"], cwd=root, check=True)
    freeze_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    result = preflight.verify_v2_manifest(manifest_path, repo_root=root, freeze_commit=freeze_commit)
    assert result["status"] == "PRE_RUN_OK", result["blockers"]
    gate_path = root / "data/research_receipts/r46_v2_preflight_receipt.json"
    preflight.persist_preflight_receipt(result, gate_path)
    assert json.loads(gate_path.read_text(encoding="utf-8")) == result["preflight_receipt"]
    return {
        "root": root,
        "manifest": manifest_path,
        "package": package_path,
        "state": root / str(manifest["evaluation_contract"]["one_shot_state_path"]),
        "gate": gate_path,
        "source_commit": source_commit,
        "freeze_commit": freeze_commit,
        "bars_v5": bars_v5,
    }


def _write_strict_green_outputs(
    fixture: dict[str, Path | str], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path | str]:
    """Construct production-shaped outputs with only the lower-level engine."""

    from parayabuck.config import load_config

    root = Path(cast(Path, fixture["root"]))
    manifest_path = Path(cast(Path, fixture["manifest"]))
    package_path = Path(cast(Path, fixture["package"]))
    state_path = Path(cast(Path, fixture["state"]))
    gate_path = Path(cast(Path, fixture["gate"]))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package_manifest = json.loads(package_path.read_text(encoding="utf-8"))
    manifest_hash = str(manifest["integrity"]["manifest_sha256"])
    package_hash = str(package_manifest["integrity"]["manifest_sha256"])
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    gate_hash = str(gate["integrity"]["receipt_sha256"])

    # The trusted loaders remain unmodified.  Their module-level roots are
    # rebound only to make the copied seam and source pins resolve inside the
    # disposable repository.
    import scripts.run_symmetric_v5_validation as seam

    seam_path = root / "scripts/run_symmetric_v5_validation.py"
    monkeypatch.setattr(seam, "__file__", str(seam_path))
    monkeypatch.setattr(seam, "REPO", root)
    monkeypatch.setattr(seam, "DATASET_V5", root / "data/historical/equity_pit_symmetric_2016_2026_v5.csv")
    monkeypatch.setattr(seam, "LIFETIMES_V5", root / "data/historical/equity_entity_lifetimes_full_v5.csv")
    monkeypatch.setattr(seam, "CA_V5", root / "data/historical/equity_survivorship_full_corporate_actions_v5.csv")
    monkeypatch.setattr(r46_runner, "REPO", root)
    package = r46_runner.load_r46_v2_package(package_path, repo_root=root)
    incumbent = r46_runner.load_incumbent_pit_inputs(repo_root=root)
    config = load_config(str(root / "config/settings.yaml"))
    dataset = build_r46_market_dataset(package, incumbent)
    source_commit = str(fixture["source_commit"])
    freeze_commit = str(fixture["freeze_commit"])
    universe, universe_version, universe_status = r46_runner._build_r46_universe(
        incumbent, config=config, test_only=False
    )
    event_boundaries = tuple(
        sorted({timestamp for schedule in package.schedules for timestamp in (schedule.entry_decision, schedule.exit_decision)})
    )
    marker_timestamps = tuple(sorted(marker.timestamp for marker in package.markers))
    cells: list[dict[str, object]] = []
    ledger_cells: list[dict[str, object]] = []
    for multiplier in COST_MULTIPLIERS:
        candidate_strategy = R46OverlayStrategy(package.schedules)
        control_strategy = _R46IncumbentStrategy(dict(r46_runner.FROZEN_V3_PARAMETERS))
        candidate_result = _run_r46_engine(
            dataset,
            candidate_strategy,
            cost_multiplier=multiplier,
            starting_equity=100_000.0,
            config=config,
            universe=universe,
            universe_version=universe_version,
            event_boundaries=event_boundaries,
            marker_timestamps=marker_timestamps,
            terminal_map=incumbent.terminal_map,
        )
        control_result = _run_r46_engine(
            dataset,
            control_strategy,
            cost_multiplier=multiplier,
            starting_equity=100_000.0,
            config=config,
            universe=universe,
            universe_version=universe_version,
            marker_timestamps=marker_timestamps,
            terminal_map=incumbent.terminal_map,
        )
        candidate_ledger = _economic_ledger_payload(
            dataset,
            candidate_result,
            strategy=candidate_strategy,
            package=package,
            cost_multiplier=multiplier,
            arm="candidate",
            manifest_sha256=manifest_hash,
            gate_sha256=gate_hash,
            universe_status=universe_status,
            universe_version=universe_version,
            source_paths=incumbent.source_paths,
            config=config,
        )
        control_ledger = _economic_ledger_payload(
            dataset,
            control_result,
            strategy=None,
            package=package,
            cost_multiplier=multiplier,
            arm="control",
            manifest_sha256=manifest_hash,
            gate_sha256=gate_hash,
            universe_status=universe_status,
            universe_version=universe_version,
            source_paths=incumbent.source_paths,
            config=config,
        )
        cells.append(
            {
                "cost_multiplier": multiplier,
                "candidate_metrics": r46_runner._result_metrics(candidate_result),
                "control_metrics": r46_runner._result_metrics(control_result),
            }
        )
        ledger_cells.append(
            {"cost_multiplier": multiplier, "candidate": candidate_ledger, "control": control_ledger}
        )
    timing_placebo = _run_timing_placebo_cell(
        package,
        incumbent,
        config=config,
        universe=universe,
        universe_version=universe_version,
        universe_status=universe_status,
        manifest_sha256=manifest_hash,
        gate_sha256=gate_hash,
        source_paths=incumbent.source_paths,
    )
    statistics = _build_statistics(
        package,
        [
            {
                **cell,
                "candidate_ledger": ledger_cells[index]["candidate"],
                "control_ledger": ledger_cells[index]["control"],
            }
            for index, cell in enumerate(cells)
        ],
        timing_placebo_cell=timing_placebo,
    )
    coverage = cast(dict[str, object], statistics["coverage_gates"])
    blockers: list[str] = []
    if int(coverage["event_episodes"]) < int(coverage["minimum_event_episodes"]):
        blockers.append("BLOCKED_INSUFFICIENT_EVENT_RATE")
    if int(coverage["holding_intervals"]) < int(coverage["minimum_holding_intervals"]):
        blockers.append("BLOCKED_INSUFFICIENT_HOLDING_INTERVALS")
    if float(coverage["missing_rate"]) > float(coverage["maximum_missing_rate"]) + 1e-9:
        blockers.append("BLOCKED_MISSINGNESS")
    if float(coverage["capacity_suppression_rate"]) > float(coverage["maximum_capacity_suppression_rate"]) + 1e-9:
        blockers.append("BLOCKED_CAPACITY_SUPPRESSION")
    if coverage["sixth_future_open_complete"] is not True or coverage["fifth_open_exit_complete"] is not True:
        blockers.append("BLOCKED_SIXTH_FUTURE_OPEN")
    if timing_placebo.get("status") != "REPORTED_NATIVE_ENGINE_DIAGNOSTIC_ONLY":
        blockers.append("BLOCKED_TIMING_PLACEBO_ENGINE_CELL")
    statistics["coverage_gates"] = {**coverage, "status": "PASS" if not blockers else "FAILED_VALID_FROZEN_TEST", "blockers": blockers}
    run_status = "PASS_RESEARCH_ONLY" if not blockers else "FAILED_VALID_FROZEN_TEST"
    source_paths = copy.deepcopy(cast(dict[str, object], ledger_cells[0]["candidate"]["source_paths"]))
    state_reference = str(manifest["evaluation_contract"]["one_shot_state_path"])
    state_raw_hash = hashlib.sha256(state_path.read_bytes()).hexdigest()
    statistics_pins = r46_runner._manifest_statistics_pins(manifest, root)
    statistics["statistics_pins"] = statistics_pins
    common = {
        "schema_version": r46_runner.ECONOMIC_RUN_SCHEMA,
        "status": run_status,
        "research_id": r46_runner.RESEARCH_ID,
        "candidate_id": "R46",
        "economic_execution": True,
        "economic_run": True,
        "research_only": True,
        "outcome_artifacts_present": True,
        "native_one_ledger": True,
        "shared_account": True,
        "separate_sleeve_blend": False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "promotion": False,
        "integration_candidate": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
    }
    results_payload: dict[str, Any] = {
        **common,
        "manifest_canonical_sha256": manifest_hash,
        "package_canonical_sha256": package_hash,
        "preflight_receipt_sha256": gate_hash,
        "governance_manifest_path": str(manifest_path.relative_to(root)),
        "data_package_manifest_path": str(package_path.relative_to(root)),
        "preflight_receipt_path": str(gate_path.relative_to(root)),
        "source_commit": source_commit,
        "freeze_commit": freeze_commit,
        "one_shot_state_path": state_reference,
        "one_shot_state_sha256": state_raw_hash,
        "dataset_digest": dataset.content_digest(),
        "source_paths": source_paths,
        "native_engine": "TerminalAwareResearchEngine",
        "engine_invocations": len(cells) * 2 + 2,
        "cost_multipliers": list(COST_MULTIPLIERS),
        "statistics_pins": statistics_pins,
        "cells": cells,
        "statistics": statistics,
        "no_best_cell_selection": True,
        "output_files": {"results": "results.json", "ledger": "ledger.json", "report": "report.json", "run_receipt": "run_receipt.json"},
    }
    ledger_payload: dict[str, Any] = {
        **common,
        "schema_version": f"{r46_runner.ECONOMIC_RUN_SCHEMA}:ledger",
        "status": r46_runner.ECONOMIC_LEDGER_STATUS,
        "source_commit": source_commit,
        "freeze_commit": freeze_commit,
        "manifest_canonical_sha256": manifest_hash,
        "package_canonical_sha256": package_hash,
        "preflight_receipt_sha256": gate_hash,
        "dataset_digest": dataset.content_digest(),
        "one_shot_state_path": state_reference,
        "one_shot_state_sha256": state_raw_hash,
        "governance_manifest_path": str(manifest_path.relative_to(root)),
        "data_package_manifest_path": str(package_path.relative_to(root)),
        "preflight_receipt_path": str(gate_path.relative_to(root)),
        "source_paths": source_paths,
        "native_engine": "TerminalAwareResearchEngine",
        "engine_invocations": len(cells) * 2 + 2,
        "statistics_pins": statistics_pins,
        "cells": ledger_cells,
        "no_best_cell_selection": True,
    }
    report_payload: dict[str, Any] = {
        **common,
        "schema_version": f"{r46_runner.ECONOMIC_RUN_SCHEMA}:report",
        "primary_estimand": statistics["primary_estimand"],
        "unchanged_incumbent_comparison": statistics["unchanged_incumbent_comparison"],
        "timing_placebo_plus_10_sessions": statistics["timing_placebo_plus_10_sessions"],
        "cost_outputs": statistics["cost_outputs"],
        "capacity": statistics["capacity"],
        "missingness": statistics["missingness"],
        "coverage_gates": statistics["coverage_gates"],
        "resampling": statistics["resampling"],
        "holm": statistics["holm"],
        "statistics_pins": statistics_pins,
        "no_thresholds_or_selection": True,
    }
    output_dir = root / "data/research/r46_strict_fixture_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    reserve_one_shot(
        state_path,
        gate_sha256=gate_hash,
        manifest_sha256=manifest_hash,
        package_sha256=package_hash,
        require_crosslinks=True,
    )
    result_hash = r46_runner._write_exclusive_payload(output_dir / "results.json", results_payload)
    ledger_hash = r46_runner._write_exclusive_payload(output_dir / "ledger.json", ledger_payload)
    report_hash = r46_runner._write_exclusive_payload(output_dir / "report.json", report_payload)
    bundle_hash = hashlib.sha256(
        r46_runner._canonical_bytes(
            {"results_sha256": result_hash, "ledger_sha256": ledger_hash, "report_sha256": report_hash}
        )
    ).hexdigest()
    receipt_payload: dict[str, Any] = {
        **common,
        "schema_version": f"{r46_runner.ECONOMIC_RUN_SCHEMA}:receipt",
        "manifest_canonical_sha256": manifest_hash,
        "package_canonical_sha256": package_hash,
        "preflight_receipt_sha256": gate_hash,
        "governance_manifest_path": str(manifest_path.relative_to(root)),
        "data_package_manifest_path": str(package_path.relative_to(root)),
        "preflight_receipt_path": str(gate_path.relative_to(root)),
        "source_commit": source_commit,
        "freeze_commit": freeze_commit,
        "runner_sha256": hashlib.sha256((root / "scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py").read_bytes()).hexdigest(),
        "verifier_sha256": hashlib.sha256((root / "scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py").read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256((root / "src/parayabuck/backtest/portfolio_engine.py").read_bytes()).hexdigest(),
        "runner_path": "scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py",
        "verifier_path": "scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py",
        "source_path": "src/parayabuck/backtest/portfolio_engine.py",
        "test_only": False,
        "one_shot": {"state_path": state_reference, "status": "CONSUMED", "output_sha256": bundle_hash},
        "outputs": {"results_sha256": result_hash, "ledger_sha256": ledger_hash, "report_sha256": report_hash, "bundle_sha256": bundle_hash},
        "cost_multipliers": list(COST_MULTIPLIERS),
        "statistics_pins": statistics_pins,
        "no_best_cell_selection": True,
        "receipt_sha256": "",
    }
    consume_one_shot(
        state_path,
        bundle_hash,
        gate_sha256=gate_hash,
        manifest_sha256=manifest_hash,
        package_sha256=package_hash,
        output_path=output_dir.resolve(),
        receipt_path=(output_dir / "run_receipt.json").resolve(),
        require_crosslinks=True,
    )
    for suffix in ("reserved", "consumed", "aborted_consumed"):
        sidecar = state_path.with_name(f"{state_path.name}.{suffix}.json")
        relative_sidecar = sidecar.relative_to(root).as_posix()
        assert subprocess.run(
            ["git", "check-ignore", "-q", "--", relative_sidecar],
            cwd=root,
            check=False,
        ).returncode == 0
    receipt_payload["receipt_sha256"] = r46_runner._canonical_hash(receipt_payload, "receipt_sha256")
    r46_runner._write_exclusive_payload(output_dir / "run_receipt.json", receipt_payload)
    return {
        "root": root,
        "gate": gate_path,
        "output_dir": output_dir,
        "results": output_dir / "results.json",
        "ledger": output_dir / "ledger.json",
        "report": output_dir / "report.json",
        "receipt": output_dir / "run_receipt.json",
    }


def test_strict_file_backed_green_fixture_verifies_without_production_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _prepare_strict_green_fixture(tmp_path, monkeypatch)
    outputs = _write_strict_green_outputs(fixture, monkeypatch)
    verified = r46_verifier.verify_economic_run_from_files(
        cast(Path, outputs["root"]),
        cast(Path, outputs["results"]),
        cast(Path, outputs["ledger"]),
        cast(Path, outputs["report"]),
        cast(Path, outputs["receipt"]),
        cast(Path, outputs["gate"]),
    )
    assert verified["status"] == "VERIFIED_ECONOMIC_RUN_FROM_FILES"
    root = Path(cast(Path, outputs["root"]))
    verifier_script = root / "scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py"
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(
        value for value in (str(root), str(root / "src"), inherited_pythonpath) if value
    )
    fresh = subprocess.run(
        [
            sys.executable,
            str(verifier_script),
            str(root),
            "--economic-final",
            str(outputs["results"]),
            str(outputs["ledger"]),
            str(outputs["report"]),
            str(outputs["receipt"]),
            str(outputs["gate"]),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": pythonpath},
        capture_output=True,
        text=True,
        check=False,
    )
    assert fresh.returncode == 0, (fresh.stdout, fresh.stderr)
    fresh_output_lines = [line for line in fresh.stdout.splitlines() if line.strip()]
    assert fresh_output_lines
    assert json.loads(fresh_output_lines[-1])["status"] == (
        "VERIFIED_ECONOMIC_RUN_FROM_FILES"
    )

    # Rehash the published output chain after each mutation.  These are
    # file-backed attacks: a changed outer result/report hash, bundle hash,
    # receipt hash, and consumed transition must not make a malformed timing
    # ledger authoritative.
    output_paths = {
        "results": cast(Path, outputs["results"]),
        "ledger": cast(Path, outputs["ledger"]),
        "report": cast(Path, outputs["report"]),
        "receipt": cast(Path, outputs["receipt"]),
    }
    baseline_ledger_payload = json.loads(output_paths["ledger"].read_text(encoding="utf-8"))
    assert any(
        item.get("symbol") != "TLT"
        for cell in baseline_ledger_payload["cells"]
        for arm in ("candidate", "control")
        for decision in cell[arm]["decisions"]
        for item in decision.get("target_weights", [])
    )
    assert any(
        fill.get("symbol") != "TLT"
        for cell in baseline_ledger_payload["cells"]
        for arm in ("candidate", "control")
        for fill in cell[arm]["fills"]
    )
    state_reference = json.loads(output_paths["receipt"].read_text(encoding="utf-8"))["one_shot"]["state_path"]
    state_path = Path(state_reference)
    if not state_path.is_absolute():
        state_path = root / state_path
    consumed_path = state_path.with_name(f"{state_path.name}.consumed.json")
    original_files = {path: path.read_bytes() for path in (*output_paths.values(), consumed_path)}
    baseline_results = json.loads(output_paths["results"].read_text(encoding="utf-8"))
    baseline_timing = copy.deepcopy(baseline_results["statistics"]["timing_placebo_plus_10_sessions"])

    def write_published(path: Path, payload: Mapping[str, Any]) -> str:
        encoded = (json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        path.write_bytes(encoded)
        return hashlib.sha256(encoded).hexdigest()

    attacks: tuple[tuple[str, Any], ...] = (
        (
            "sparse_delete_decisions",
            lambda timing: timing["candidate_ledger"].pop("decisions"),
        ),
        (
            "duplicate_schedule",
            lambda timing: timing["candidate_ledger"]["event_schedules"].append(
                copy.deepcopy(timing["candidate_ledger"]["event_schedules"][-1])
            ),
        ),
        (
            "alter_marker_field",
            lambda timing: timing["candidate_ledger"]["marker_records"][0].__setitem__("source", "forged-source"),
        ),
        (
            "bad_inner_hash",
            lambda timing: timing["candidate_ledger"].__setitem__("ledger_sha256", "0" * 64),
        ),
        (
            "bad_source_pin",
            lambda timing: timing["source_binding"].__setitem__("manifest_canonical_sha256", "f" * 64),
        ),
    )
    for name, mutate in attacks:
        for path, content in original_files.items():
            path.write_bytes(content)
        results = json.loads(output_paths["results"].read_text(encoding="utf-8"))
        ledger = json.loads(output_paths["ledger"].read_text(encoding="utf-8"))
        report = json.loads(output_paths["report"].read_text(encoding="utf-8"))
        timing = results["statistics"]["timing_placebo_plus_10_sessions"]
        mutate(timing)
        # A rehash attack must get past the inner canonical hash whenever the
        # mutation is otherwise structurally admissible; the bad-hash case is
        # deliberately left unhashed.
        if name not in {"bad_inner_hash", "bad_source_pin"}:
            candidate = timing["candidate_ledger"]
            candidate["ledger_sha256"] = r46_runner._canonical_hash(candidate, "ledger_sha256")
        report["timing_placebo_plus_10_sessions"] = copy.deepcopy(timing)
        write_published(output_paths["results"], results)
        write_published(output_paths["ledger"], ledger)
        write_published(output_paths["report"], report)
        receipt = json.loads(output_paths["receipt"].read_text(encoding="utf-8"))
        output_hashes = {
            "results_sha256": r46_verifier._published_payload_hash(results),
            "ledger_sha256": r46_verifier._published_payload_hash(ledger),
            "report_sha256": r46_verifier._published_payload_hash(report),
        }
        bundle_hash = hashlib.sha256(
            json.dumps(output_hashes, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        receipt["outputs"].update({**output_hashes, "bundle_sha256": bundle_hash})
        receipt["one_shot"]["output_sha256"] = bundle_hash
        receipt["receipt_sha256"] = r46_runner._canonical_hash(receipt, "receipt_sha256")
        write_published(output_paths["receipt"], receipt)
        consumed = json.loads(consumed_path.read_text(encoding="utf-8"))
        consumed["output_sha256"] = bundle_hash
        consumed["integrity"]["transition_sha256"] = r46_runner._canonical_hash(
            consumed,
            "transition_sha256",
            nested=True,
        )
        write_published(consumed_path, consumed)
        persisted_results = json.loads(output_paths["results"].read_text(encoding="utf-8"))
        persisted_report = json.loads(output_paths["report"].read_text(encoding="utf-8"))
        persisted_timing = persisted_results["statistics"]["timing_placebo_plus_10_sessions"]
        assert persisted_report["timing_placebo_plus_10_sessions"] == persisted_timing
        for output_path in output_paths.values():
            if output_path.name == "run_receipt.json":
                continue
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            assert hashlib.sha256(output_path.read_bytes()).hexdigest() == r46_verifier._published_payload_hash(payload)
        if name in {"sparse_delete_decisions", "bad_inner_hash"}:
            with pytest.raises(ValueError, match="LEDGER_HASH_MISMATCH|economic_decisions"):
                _verify_economic_structural(persisted_timing["candidate_ledger"])
        elif name == "duplicate_schedule":
            assert persisted_timing["candidate_ledger"]["event_schedules"] != baseline_timing["candidate_ledger"]["event_schedules"]
        elif name == "alter_marker_field":
            assert persisted_timing["candidate_ledger"]["marker_records"] != baseline_timing["candidate_ledger"]["marker_records"]
        else:
            assert persisted_timing["source_binding"]["manifest_canonical_sha256"] != (
                persisted_timing["candidate_ledger"]["manifest_canonical_sha256"]
            )

    def shift_target_weight(inner: dict[str, Any], *, require_tlt: bool = False) -> None:
        for decision in inner["decisions"]:
            weights = decision.get("target_weights", [])
            symbols = {str(item.get("symbol")) for item in weights if isinstance(item, Mapping)}
            if require_tlt and "TLT" not in symbols:
                continue
            delta = 0.001
            non_tlt = next((item for item in weights if item.get("symbol") != "TLT"), None)
            if non_tlt is None:
                forged = {
                    "owner": "incumbent_v3",
                    "reason": "unchanged_incumbent_v3",
                    "symbol": "AAA",
                    "weight": delta,
                }
                decision["target_weights"].append(copy.deepcopy(forged))
                decision["desired_target_weights"].append(copy.deepcopy(forged))
            else:
                for field in ("target_weights", "desired_target_weights"):
                    for item in decision[field]:
                        if item.get("symbol") != "TLT":
                            item["weight"] = float(item["weight"]) + delta
                            break
            decision["target_cash_weight"] = float(decision["target_cash_weight"]) - delta
            decision["desired_target_cash_weight"] = float(decision["desired_target_cash_weight"]) - delta
            return
        raise AssertionError("fixture has no suitable target")

    def swap_valid_target_symbol(inner: dict[str, Any]) -> None:
        all_symbols = {
            str(item.get("symbol"))
            for decision in inner["decisions"]
            for item in decision.get("target_weights", [])
            if isinstance(item, Mapping) and str(item.get("symbol")) != "TLT"
        }
        for decision in inner["decisions"]:
            weights = decision.get("target_weights", [])
            current = {str(item.get("symbol")) for item in weights if isinstance(item, Mapping)}
            incumbent = next((item for item in weights if item.get("symbol") != "TLT"), None)
            replacement = next((symbol for symbol in sorted(all_symbols) if symbol not in current), None)
            if incumbent is not None and replacement is not None:
                old_symbol = incumbent["symbol"]
                for field in ("target_weights", "desired_target_weights"):
                    for item in decision[field]:
                        if item.get("symbol") == old_symbol:
                            item["symbol"] = replacement
                return
        for decision in inner["decisions"]:
            if any(item.get("symbol") == "TLT" for item in decision.get("target_weights", [])):
                forged = {
                    "owner": "incumbent_v3",
                    "reason": "unchanged_incumbent_v3",
                    "symbol": "AAA",
                    "weight": 0.001,
                }
                decision["target_weights"].append(copy.deepcopy(forged))
                decision["desired_target_weights"].append(copy.deepcopy(forged))
                decision["target_cash_weight"] = float(decision["target_cash_weight"]) - 0.001
                decision["desired_target_cash_weight"] = float(decision["desired_target_cash_weight"]) - 0.001
                return
        raise AssertionError("fixture has no valid target-symbol swap")

    def couple_unfilled(inner: dict[str, Any], decision_timestamp: str) -> None:
        for row in (*inner["decisions"], *inner["rebalances"]):
            if row.get("decision_timestamp") == decision_timestamp:
                row["unfilled_notional"] = float(row.get("unfilled_notional", 0.0)) + 1.0

    def first_non_tlt_fill(inner: dict[str, Any]) -> dict[str, Any]:
        for fill in inner["fills"]:
            if str(fill.get("symbol")) != "TLT":
                return fill
        raise AssertionError("fixture has no non-TLT fill to attack")

    def rehash_bundle(results: dict[str, Any], ledger: dict[str, Any], report: dict[str, Any]) -> None:
        write_published(output_paths["results"], results)
        write_published(output_paths["ledger"], ledger)
        write_published(output_paths["report"], report)
        receipt = json.loads(output_paths["receipt"].read_text(encoding="utf-8"))
        output_hashes = {
            "results_sha256": r46_verifier._published_payload_hash(results),
            "ledger_sha256": r46_verifier._published_payload_hash(ledger),
            "report_sha256": r46_verifier._published_payload_hash(report),
        }
        bundle_hash = hashlib.sha256(
            json.dumps(output_hashes, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        receipt["outputs"].update({**output_hashes, "bundle_sha256": bundle_hash})
        receipt["one_shot"]["output_sha256"] = bundle_hash
        receipt["receipt_sha256"] = r46_runner._canonical_hash(receipt, "receipt_sha256")
        write_published(output_paths["receipt"], receipt)
        consumed = json.loads(consumed_path.read_text(encoding="utf-8"))
        consumed["output_sha256"] = bundle_hash
        consumed["integrity"]["transition_sha256"] = r46_runner._canonical_hash(
            consumed,
            "transition_sha256",
            nested=True,
        )
        write_published(consumed_path, consumed)

    def mutate_candidate_target(ledger: dict[str, Any]) -> None:
        candidate = ledger["cells"][0]["candidate"]
        shift_target_weight(candidate)
        candidate["ledger_sha256"] = r46_runner._canonical_hash(candidate, "ledger_sha256")

    def mutate_both_targets(ledger: dict[str, Any]) -> None:
        candidate = ledger["cells"][0]["candidate"]
        control = ledger["cells"][0]["control"]
        shift_target_weight(candidate)
        shift_target_weight(control)
        candidate["ledger_sha256"] = r46_runner._canonical_hash(candidate, "ledger_sha256")
        control["ledger_sha256"] = r46_runner._canonical_hash(control, "ledger_sha256")

    def mutate_valid_symbol_swap(ledger: dict[str, Any]) -> None:
        candidate = ledger["cells"][0]["candidate"]
        swap_valid_target_symbol(candidate)
        candidate["ledger_sha256"] = r46_runner._canonical_hash(candidate, "ledger_sha256")

    def mutate_fresh_event_target(ledger: dict[str, Any]) -> None:
        candidate = ledger["cells"][0]["candidate"]
        shift_target_weight(candidate, require_tlt=True)
        candidate["ledger_sha256"] = r46_runner._canonical_hash(candidate, "ledger_sha256")

    def mutate_delete_non_tlt_fill(ledger: dict[str, Any]) -> None:
        candidate = ledger["cells"][0]["candidate"]
        fill = first_non_tlt_fill(candidate)
        decision_timestamp = str(fill["decision_timestamp"])
        candidate["fills"].remove(fill)
        couple_unfilled(candidate, decision_timestamp)
        candidate["ledger_sha256"] = r46_runner._canonical_hash(candidate, "ledger_sha256")

    def mutate_shrink_non_tlt_fill(ledger: dict[str, Any]) -> None:
        candidate = ledger["cells"][0]["candidate"]
        fill = first_non_tlt_fill(candidate)
        decision_timestamp = str(fill["decision_timestamp"])
        fill["qty"] = float(fill["qty"]) * 0.5
        fill["notional"] = float(fill["qty"]) * float(fill["price"])
        couple_unfilled(candidate, decision_timestamp)
        candidate["ledger_sha256"] = r46_runner._canonical_hash(candidate, "ledger_sha256")

    def mutate_move_non_tlt_fill(ledger: dict[str, Any]) -> None:
        candidate = ledger["cells"][0]["candidate"]
        fill = first_non_tlt_fill(candidate)
        decision_timestamps = [str(item["decision_timestamp"]) for item in candidate["decisions"]]
        replacement = next(timestamp for timestamp in decision_timestamps if timestamp != fill["decision_timestamp"])
        original_timestamp = str(fill["decision_timestamp"])
        fill["decision_timestamp"] = replacement
        couple_unfilled(candidate, original_timestamp)
        couple_unfilled(candidate, replacement)
        candidate["ledger_sha256"] = r46_runner._canonical_hash(candidate, "ledger_sha256")

    def mutate_marker_exclusion(ledger: dict[str, Any]) -> None:
        candidate = ledger["cells"][0]["candidate"]
        candidate["marker_records"].pop()
        candidate["ledger_sha256"] = r46_runner._canonical_hash(candidate, "ledger_sha256")

    def mutate_dividend_owner(ledger: dict[str, Any]) -> None:
        candidate = ledger["cells"][0]["candidate"]
        event = candidate["dividend_events"][0]
        event["owner"] = "incumbent_v3" if event.get("owner") == "r46" else "r46"
        candidate["ledger_sha256"] = r46_runner._canonical_hash(candidate, "ledger_sha256")

    def mutate_terminal_owner(ledger: dict[str, Any]) -> None:
        candidate = ledger["cells"][0]["candidate"]
        if not candidate.get("terminal_audit"):
            raise AssertionError("fixture has no terminal audit row to attack")
        event = candidate["terminal_audit"][0]
        event["owner"] = "incumbent_v3" if event.get("owner") == "r46" else "r46"
        candidate["ledger_sha256"] = r46_runner._canonical_hash(candidate, "ledger_sha256")

    rehashed_oracle_attacks: tuple[tuple[str, Any], ...] = (
        ("candidate_only_target_forgery", mutate_candidate_target),
        ("both_arms_target_forgery", mutate_both_targets),
        ("valid_symbol_swap_with_cash_and_fill_surface", mutate_valid_symbol_swap),
        ("fresh_event_target_instead_of_cached", mutate_fresh_event_target),
        ("delete_non_tlt_fill_with_coupled_unfilled", mutate_delete_non_tlt_fill),
        ("shrink_non_tlt_fill_with_coupled_unfilled", mutate_shrink_non_tlt_fill),
        ("move_non_tlt_fill_with_coupled_unfilled", mutate_move_non_tlt_fill),
        ("marker_exclusion", mutate_marker_exclusion),
        ("dividend_owner_flip", mutate_dividend_owner),
        ("terminal_owner_flip", mutate_terminal_owner),
    )
    for name, mutate in rehashed_oracle_attacks:
        for path, content in original_files.items():
            path.write_bytes(content)
        results = json.loads(output_paths["results"].read_text(encoding="utf-8"))
        ledger = json.loads(output_paths["ledger"].read_text(encoding="utf-8"))
        report = json.loads(output_paths["report"].read_text(encoding="utf-8"))
        mutate(ledger)
        rehash_bundle(results, ledger, report)
        with pytest.raises(ValueError):
            r46_verifier.verify_economic_run_from_files(
                cast(Path, outputs["root"]),
                cast(Path, outputs["results"]),
                cast(Path, outputs["ledger"]),
                cast(Path, outputs["report"]),
                cast(Path, outputs["receipt"]),
                cast(Path, outputs["gate"]),
            )
    for path, content in original_files.items():
        path.write_bytes(content)
