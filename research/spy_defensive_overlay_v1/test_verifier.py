from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from research.spy_defensive_overlay_v1.verify_results import (
    ARMS,
    COSTS,
    VerificationExpectations,
    verify_campaign,
)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path, VerificationExpectations]:
    repo = tmp_path / "repo"
    output = repo / "research/spy_defensive_overlay_v1"
    output.mkdir(parents=True)
    protocol = output / "FROZEN_PROTOCOL.md"
    v3 = repo / "src/parayabuck/portfolio/equity_residual_momentum_v3.py"
    protocol.write_text("frozen synthetic protocol\n", encoding="utf-8")
    v3.parent.mkdir(parents=True)
    v3.write_text("# unchanged V3 fixture\n", encoding="utf-8")

    first = datetime(2021, 4, 19, tzinfo=UTC)
    dates = [first + timedelta(days=index) for index in range(253)]
    dates.append(datetime(2022, 1, 3, tzinfo=UTC))
    bar_rows: list[dict[str, object]] = []
    for timestamp in dates:
        for symbol, price in (("SPY", 100.0), ("SH", 20.0)):
            bar_rows.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "symbol": symbol,
                    "timeframe": "1d",
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 1_000,
                    "market": "equity",
                }
            )
    bars = repo / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
    _write_csv(
        bars,
        ["timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market"],
        bar_rows,
    )
    lifetimes = repo / "data/historical/equity_entity_lifetimes_full_v5.csv"
    _write_csv(
        lifetimes,
        ["canonical_symbol", "last_observed_bar", "current_status", "pit_quality_tier", "terminal_event_class"],
        [
            {
                "canonical_symbol": symbol,
                "last_observed_bar": dates[-1].isoformat(),
                "current_status": "ACTIVE",
                "pit_quality_tier": "TIER_A_VERIFIED_PIT",
                "terminal_event_class": "",
            }
            for symbol in ("SPY", "SH")
        ],
    )
    actions = repo / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
    _write_csv(actions, ["action_type", "symbol", "ex_date", "rate", "raw_id"], [])
    hashes = {str(path.relative_to(repo)): _hash(path) for path in (bars, lifetimes, actions)}
    main_sha = "1" * 40
    branch = "research/spy-defensive-overlay-v1"
    manifest = {
        "campaign": "equity_spy_defensive_overlay_v1",
        "cleaned_main_sha": main_sha,
        "research_head_at_run": "2" * 40,
        "branch": branch,
        "protocol_sha256": _hash(protocol),
        "data_hashes": hashes,
        "v3_source_sha256": _hash(v3),
        "paper_config_unchanged": True,
        "live_unchanged_disabled": True,
    }
    decision_dates = (dates[-2], dates[-1])
    source = [
        {"symbol": "AAA", "weight": 0.1, "rank": 1},
        {"symbol": "BBB", "weight": 0.1, "rank": 2},
    ]
    audits: dict[str, object] = {}
    runs: dict[str, object] = {}
    for arm in ARMS:
        for cost in COSTS:
            key = f"{arm}_{cost}x"
            scale = 1.0 if arm == "A_incumbent" else 0.45 / 0.55
            overlay = 0.1 if arm == "C_defensive_sh" else 0.0
            target = {"AAA": 0.1 * scale, "BBB": 0.1 * scale}
            if overlay:
                target["SH"] = overlay
            rebalance_rows = [
                {
                    "decision_timestamp": timestamp.isoformat(),
                    "target_weights": sorted(target.items()),
                    "target_cash_weight": 1.0 - sum(target.values()),
                    "cost": 0.0,
                    "realized_turnover": 0.0,
                    "risk_overlay_halted": False,
                    "risk_overlay_reasons": [],
                }
                for timestamp in decision_dates
            ]
            diagnostics = {
                timestamp.isoformat(): {
                    "arm": arm,
                    "regime": "DEFENSIVE",
                    "trend_return": 0.0,
                    "signal_observations": 253,
                    "intended_v3_exposure": 0.55 if arm == "A_incumbent" else 0.45,
                    "pre_overlay_v3_exposure": 0.2 * scale,
                    "intended_overlay_sh_exposure": overlay,
                    "realized_overlay_sh_exposure": overlay,
                    "v3_owned_sh": 0.0,
                    "total_sh_exposure": overlay,
                    "source_v3_weights": source,
                    "v3_scale": scale,
                    "sh_slot_reserved": bool(overlay),
                    "maximum_non_sh_v3_positions_after_overlay": 5 if overlay else 6,
                    "no_unused_allocation_reallocated": True,
                }
                for timestamp in decision_dates
            }
            audits[key] = {
                "arm": arm,
                "cost_multiplier": cost,
                "starting_equity": 100_000.0,
                "ending_equity": 100_000.0,
                "curve": [[decision_dates[-1].isoformat(), 100_000.0]],
                "fills": [],
                "terminal_events": [],
                "rebalances": rebalance_rows,
                "decision_diagnostics": diagnostics,
                "dividend_total": 0.0,
                "slippage_cost_total": 0.0,
                "spread_cost_total": 0.0,
                "fee_total": 0.0,
                "state_cash": 100_000.0,
                "state_holdings": {},
                "state_pending_orders": [],
            }
            capacity_rows = [
                {"timestamp": timestamp.isoformat(), "target_weights": sorted(target.items())}
                for timestamp in decision_dates
            ]
            runs[key] = {
                "arm": arm,
                "cost_multiplier": cost,
                "coverage": {
                    "scheduled_opportunities": 2,
                    "recorded_decisions": 2,
                    "skipped_decisions": 0,
                    "accounting_complete": True,
                    "skips": [],
                    "development_decisions": 1,
                    "validation_decisions": 1,
                },
                "capacity": {"rows": capacity_rows, "defensive_decisions": 2},
            }
    results = {"campaign": manifest["campaign"], "data_hashes": hashes, "runs": runs}
    for name, payload in (
        ("run_manifest.json", manifest),
        ("campaign_results.json", results),
        ("execution_audit.json", audits),
    ):
        (output / name).write_text(json.dumps(payload), encoding="utf-8")
    expected = VerificationExpectations(
        cleaned_main_sha=main_sha,
        branch=branch,
        data_hashes=hashes,
        full_start=decision_dates[0],
        development_end=datetime(2021, 12, 31, 23, 59, 59, tzinfo=UTC),
        validation_start=decision_dates[1],
        full_end=decision_dates[1],
    )
    return repo, output, expected


def _fake_git(repo: Path, *args: str) -> str:
    del repo
    return "1" * 40 if args == ("rev-parse", "main") else "research/spy-defensive-overlay-v1"


def test_tiny_valid_fixture_passes_and_writes_report(tmp_path: Path) -> None:
    repo, output, expected = _fixture(tmp_path)
    result = verify_campaign(output, repo, expectations=expected, git_reader=_fake_git)
    assert result["status"] == "PASS"
    assert result["summary"]["failed"] == 0
    assert json.loads((output / "independent_verification.json").read_text())["status"] == "PASS"


def test_signal_tamper_fails_closed(tmp_path: Path) -> None:
    repo, output, expected = _fixture(tmp_path)
    audit_path = output / "execution_audit.json"
    audits = json.loads(audit_path.read_text())
    first_run = next(iter(audits.values()))
    first_diagnostic = next(iter(first_run["decision_diagnostics"].values()))
    first_diagnostic["trend_return"] = 0.25
    audit_path.write_text(json.dumps(audits), encoding="utf-8")

    result = verify_campaign(output, repo, expectations=expected, git_reader=_fake_git)
    assert result["status"] == "FAIL"
    assert any(row["name"].startswith("signal.") and row["status"] == "FAIL" for row in result["checks"])


def test_missing_artifact_still_writes_fail_report(tmp_path: Path) -> None:
    repo, output, expected = _fixture(tmp_path)
    (output / "execution_audit.json").unlink()
    result = verify_campaign(output, repo, expectations=expected, git_reader=_fake_git)
    assert result["status"] == "FAIL"
    assert result["checks"][-1]["name"] == "verification.completed_without_exception"
    assert (output / "independent_verification.json").exists()
