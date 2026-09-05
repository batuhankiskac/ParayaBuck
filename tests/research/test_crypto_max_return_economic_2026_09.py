from __future__ import annotations

import json
import hashlib
import csv
import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data.aggregator import aggregate_historical_bars

economic = pytest.importorskip("scripts.research.crypto_max_return_economic_2026_09")


SYMBOLS = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "DOGE/USD")
FULL_SYMBOLS = SYMBOLS + (
    "AVAX/USD", "LINK/USD", "LTC/USD", "BCH/USD", "DOT/USD", "UNI/USD",
    "AAVE/USD", "ARB/USD", "FIL/USD", "RENDER/USD", "ONDO/USD", "HYPE/USD",
    "SHIB/USD", "GRT/USD", "CRV/USD", "PEPE/USD",
)


def _bars(days: int = 130) -> list[Bar]:
    start = datetime(2021, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for day in range(days):
        timestamp = start + timedelta(days=day)
        for index, symbol in enumerate(SYMBOLS):
            close = 100.0 + index + day * (0.1 + index * 0.001)
            bars.append(Bar(timestamp, symbol, "1d", close, close, close, close, 1_000_000.0, Market.CRYPTO))
    return bars


def _hourly_bars(days: int = 26) -> list[Bar]:
    start = datetime(2021, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for day in range(days):
        for hour in range(24):
            timestamp = start + timedelta(days=day, hours=hour)
            for index, symbol in enumerate(("BTC/USD", "ETH/USD")):
                close = 100.0 + index + day * 0.1 + hour * 0.001
                bars.append(Bar(timestamp, symbol, "1h", close, close, close, close, 1_000.0, Market.CRYPTO))
    return bars


def _hourly_engine_bars(days: int = 600) -> list[Bar]:
    start = datetime(2021, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    for day in range(days):
        for hour in range(24):
            timestamp = start + timedelta(days=day, hours=hour)
            for index, symbol in enumerate(FULL_SYMBOLS):
                base = 100.0 + index + day * (0.1 + index * 0.001) + hour * 0.0001
                shock = ((day * (index + 2) + hour * (index + 3)) % 7) - 3
                close = base * (1.0 + 0.004 * shock)
                bars.append(Bar(timestamp, symbol, "1h", close, close, close, close, 1_000_000.0, Market.CRYPTO))
    return bars


def test_build_strategy_has_three_frozen_arms_and_rejects_unknown() -> None:
    assert economic.build_strategy("max").name == "crypto_max_22d_hyst4"
    assert economic.build_strategy("low_variance").name == "crypto_low_variance_hysteresis_v1"
    assert economic.build_strategy("dollar_volume").name == "crypto_lagged_dollar_volume_hyst4_research"
    with pytest.raises(ValueError, match="Unknown crypto economic arm"):
        economic.build_strategy("other")


def test_run_case_uses_warmup_and_native_96h_next_day_execution() -> None:
    case = economic.run_case(load_config("config/settings.yaml"), _hourly_engine_bars(), arm="max", window="full", cost_multiplier=1.0)
    result = case.result
    assert result.fills
    assert all(fill.timestamp > fill.decision_timestamp for fill in result.fills)
    scheduled = [record.decision_timestamp for record in result.rebalances if record.reason == "scheduled"]
    assert scheduled
    deltas = [right - left for left, right in zip(scheduled, scheduled[1:])]
    assert deltas[0] == timedelta(days=2)  # warmup reaches first eligible decision
    assert all(delta == timedelta(days=4) for delta in deltas[1:])
    assert all(record.target_cash_weight >= 0.95 for record in result.rebalances)
    assert all(weight <= 0.05 + 1e-12 for record in result.rebalances for _, weight in record.target_weights)


def test_case_payload_reconciles_fees_cash_ledger_and_terminal_mark() -> None:
    case = economic.run_case(load_config("config/settings.yaml"), _hourly_engine_bars(), arm="max", window="full", cost_multiplier=2.0)
    payload = economic.case_payload(case)
    result = case.result
    assert payload["virtual_holdings_only"] is False
    assert payload["equity_curve"][-1] == pytest.approx(result.ending_equity)
    assert payload["fees"]["fee_total"] == pytest.approx(sum(fill.fee for fill in result.fills))
    assert len(result.curve_timestamps) == len(result.equity_curve)
    assert all(payload["equity_timestamps"][i] <= payload["equity_timestamps"][i + 1] for i in range(len(payload["equity_timestamps"]) - 1))
    assert all("fee" in fill and "timestamp" in fill for fill in payload["fills"])
    assert payload["cash_ledger"]
    final_ledger = payload["cash_ledger"][-1]
    marked_positions = sum(position["value"] for position in final_ledger["positions"].values())
    assert final_ledger["cash"] + marked_positions == pytest.approx(final_ledger["equity"])
    assert final_ledger["equity"] == pytest.approx(result.ending_equity)


def test_candidate_and_incumbent_share_native_schedule_and_universe() -> None:
    config = load_config("config/settings.yaml")
    candidate = economic.run_case(config, _hourly_engine_bars(), arm="max", window="full", cost_multiplier=1.0)
    incumbent = economic.run_case(config, _hourly_engine_bars(), arm="low_variance", window="full", cost_multiplier=1.0)
    candidate_schedule = [r.decision_timestamp for r in candidate.result.rebalances if r.reason == "scheduled"]
    incumbent_schedule = [r.decision_timestamp for r in incumbent.result.rebalances if r.reason == "scheduled"]
    assert candidate_schedule == incumbent_schedule
    assert candidate.universe_hash and incumbent.universe_hash
    assert candidate.strategy.decision_traces
    assert incumbent.strategy.decision_traces
    assert [t["decision_timestamp"] for t in candidate.strategy.decision_traces] == [t["decision_timestamp"] for t in incumbent.strategy.decision_traces]


def test_verify_frozen_inputs_fails_closed_before_any_economic_run(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    receipt = tmp_path / "receipt.json"
    manifest.write_text(json.dumps({"status": "DRAFT"}), encoding="utf-8")
    receipt.write_text(json.dumps({}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="BLOCKED_MANIFEST_NOT_FROZEN"):
        economic.verify_frozen_inputs(manifest, receipt)
    manifest.write_text(json.dumps({"status": "FROZEN_NOVELTY", "manifest_sha256": "m"}), encoding="utf-8")
    receipt.write_text(json.dumps({"manifest_sha256": "stale"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="BLOCKED_SOURCE_PIN_MISSING"):
        economic.verify_frozen_inputs(manifest, receipt)


def test_completed_daily_buckets_are_prefix_invariant_to_same_day_future_hours() -> None:
    base = _hourly_bars()
    cutoff = datetime(2021, 1, 25, tzinfo=UTC)
    prefix = [bar for bar in base if bar.timestamp < cutoff + timedelta(hours=1)]
    extended = list(prefix) + [bar for bar in base if cutoff + timedelta(hours=1) <= bar.timestamp < cutoff + timedelta(days=1)]
    base_daily = aggregate_historical_bars(prefix, "1d", require_complete=True)
    extended_daily = aggregate_historical_bars(extended, "1d", require_complete=True)
    before_base = [(bar.timestamp, bar.symbol, bar.close) for bar in base_daily if bar.timestamp < cutoff]
    before_extended = [(bar.timestamp, bar.symbol, bar.close) for bar in extended_daily if bar.timestamp < cutoff]
    assert before_extended == before_base
    assert all(bar.timestamp < cutoff for bar in extended_daily if bar.timestamp < cutoff)


def test_interior_missing_completed_day_does_not_shift_daily_timestamps() -> None:
    bars = [bar for bar in _hourly_bars() if bar.timestamp.date() != datetime(2021, 1, 8, tzinfo=UTC).date()]
    daily = aggregate_historical_bars(bars, "1d", require_complete=True)
    timestamps = sorted({bar.timestamp for bar in daily})
    assert datetime(2021, 1, 8, tzinfo=UTC) not in timestamps
    after_gap = [timestamp for timestamp in timestamps if timestamp > datetime(2021, 1, 8, tzinfo=UTC)]
    assert after_gap and after_gap[0] == datetime(2021, 1, 9, tzinfo=UTC)


def test_terminal_protocol_keeps_positions_marked_without_forced_liquidation() -> None:
    case = economic.run_case(load_config("config/settings.yaml"), _hourly_engine_bars(), arm="max", window="full", cost_multiplier=1.0)
    assert case.result.state.holdings
    assert case.result.curve_timestamps[-1] == _hourly_engine_bars()[-1].timestamp
    assert case.engine.cash_ledger[-1]["equity"] == pytest.approx(case.result.ending_equity)


def test_run_case_decisions_are_prefix_invariant_to_unfinished_same_day_hours() -> None:
    full = _hourly_engine_bars()
    cutoff = datetime(2021, 4, 25, tzinfo=UTC)
    prefix = [bar for bar in full if bar.timestamp < cutoff + timedelta(hours=1)]
    extended = list(prefix) + [
        replace(bar, close=1_000_000.0, open=1_000_000.0, high=1_000_000.0, low=1_000_000.0)
        for bar in full
        if cutoff + timedelta(hours=1) <= bar.timestamp < cutoff + timedelta(days=1)
    ]
    config = load_config("config/settings.yaml")
    before = economic.run_case(config, prefix, arm="max", window="full", cost_multiplier=1.0)
    after = economic.run_case(config, extended, arm="max", window="full", cost_multiplier=1.0)
    before_traces = [trace for trace in before.strategy.decision_traces if trace["decision_timestamp"] <= cutoff]
    after_traces = [trace for trace in after.strategy.decision_traces if trace["decision_timestamp"] <= cutoff]
    assert len(before_traces) == len(after_traces) > 0
    for left, right in zip(before_traces, after_traces, strict=True):
        assert left["decision_timestamp"] == right["decision_timestamp"]
        assert left["candidate_scores"] == right["candidate_scores"]
        assert left["target_symbols"] == right["target_symbols"]


def test_clean_cli_run_entrypoint_fails_closed_on_draft_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    receipt = tmp_path / "receipt.json"
    output = tmp_path / "output.json"
    manifest.write_text(json.dumps({"status": "DRAFT"}), encoding="utf-8")
    receipt.write_text(json.dumps({}), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.research.crypto_max_return_economic_2026_09",
            "--run",
            "--manifest",
            str(manifest),
            "--receipt",
            str(receipt),
            "--output",
            str(output),
        ],
        cwd=economic.REPO,
        env={key: value for key, value in __import__("os").environ.items() if key != "PYTHONPATH"},
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert "BLOCKED_MANIFEST_NOT_FROZEN" in completed.stdout + completed.stderr


def _gate_cells() -> list[dict[str, object]]:
    cells: list[dict[str, object]] = []
    for window in economic.WINDOWS:
        for arm in economic.ARMS:
            for cost in economic.COST_MULTIPLIERS:
                total = 0.10 if arm != "dollar_volume" else 0.05
                sharpe = 2.0 if arm == "max" and cost == 1.0 else 1.0
                cells.append({
                    "arm": arm,
                    "window": window,
                    "cost_multiplier": cost,
                    "metrics": {"total_return": total, "cagr": 0.05, "sharpe": sharpe, "max_drawdown": 0.10, "turnover": 0.1},
                    "cash_ledger": [{"cash": 100_000.0, "equity": 100_000.0, "fees_to_date": 0.0, "slippage_to_date": 0.0, "spread_to_date": 0.0, "positions": {}}],
                    "equity_curve": [100_000.0],
                    "equity_timestamps": ["2021-01-01T00:00:00+00:00"],
                    "fills": [],
                    "fees": {"fee_total": 0.0},
                    "terminal_policy": "final_mark_no_forced_sale",
                    "forced_liquidation": False,
                })
    return cells


def test_evaluate_results_requires_exact_18_distinct_cells() -> None:
    cells = _gate_cells()
    assert economic.evaluate_results(cells)["status"] == "RETAIN_FOR_RESEARCH"
    assert economic.evaluate_results(cells[:-1])["reason"] == "incomplete_or_duplicate_18_cell_matrix"
    duplicate = cells[:-1] + [dict(cells[0])]
    assert economic.evaluate_results(duplicate)["reason"] == "incomplete_or_duplicate_18_cell_matrix"


def test_evaluate_results_blocks_missing_nonfinite_but_zero_is_valid_data() -> None:
    cells = _gate_cells()
    cells[0]["metrics"] = {"total_return": 0.0, "cagr": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "turnover": 0.0}
    assert economic.evaluate_results(cells)["status"] == "REJECT"
    cells = _gate_cells()
    cells[0]["metrics"] = {"total_return": float("nan"), "cagr": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "turnover": 0.0}
    assert economic.evaluate_results(cells)["reason"] == "nonfinite_or_missing_required_metrics"
    cells = _gate_cells()
    cells[0]["cash_ledger"] = [{"cash": float("inf"), "equity": 100_000.0}]
    assert economic.evaluate_results(cells)["reason"] == "BLOCKED_EVIDENCE_NONFINITE_LEDGER:cash"


def test_validate_case_evidence_accepts_real_payload_and_blocks_structural_corruption() -> None:
    case = economic.run_case(load_config("config/settings.yaml"), _hourly_engine_bars(), arm="max", window="full", cost_multiplier=1.0)
    payload = economic.case_payload(case)
    economic.validate_case_evidence(payload)
    broken = dict(payload)
    broken["equity_timestamps"] = broken["equity_timestamps"][:-1]
    with pytest.raises(RuntimeError, match="BLOCKED_EVIDENCE_EQUITY_CURVE_ALIGNMENT"):
        economic.validate_case_evidence(broken)
    broken = dict(payload)
    broken["cash_ledger"] = [dict(row) for row in payload["cash_ledger"]]
    broken["cash_ledger"][-1] = dict(broken["cash_ledger"][-1], equity=float("nan"))
    with pytest.raises(RuntimeError, match="BLOCKED_EVIDENCE_NONFINITE_LEDGER:equity"):
        economic.validate_case_evidence(broken)
    broken = dict(payload)
    broken["cash_ledger"] = [dict(row) for row in payload["cash_ledger"]]
    broken["cash_ledger"][-1] = dict(broken["cash_ledger"][-1], positions={"BTC/USD": {"qty": 1.0, "mark": 1.0, "value": 1.0}})
    with pytest.raises(RuntimeError, match="BLOCKED_EVIDENCE_CASH_POSITION_RECONCILIATION"):
        economic.validate_case_evidence(broken)


def test_clean_cli_run_writes_18_cell_json_from_frozen_synthetic_csv(tmp_path: Path) -> None:
    bars_path = tmp_path / "synthetic_crypto_1h.csv"
    start = datetime(2020, 1, 1, tzinfo=UTC)
    end = datetime(2026, 8, 1, tzinfo=UTC)
    with bars_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "symbol", "open", "high", "low", "close", "volume"))
        day = 0
        timestamp = start
        while timestamp <= end:
            for hour in range(24):
                current = timestamp + timedelta(hours=hour)
                for index, symbol in enumerate(("BTC/USD", "ETH/USD")):
                    base = 100.0 + index + day * 0.02 + hour * 0.001
                    shock = ((day * (index + 2) + hour * (index + 3)) % 7) - 3
                    close = base * (1.0 + 0.004 * shock)
                    writer.writerow((current.isoformat().replace("+00:00", "Z"), symbol, close, close, close, close, 1_000_000.0))
            day += 1
            timestamp += timedelta(days=1)
    bars_hash = hashlib.sha256(bars_path.read_bytes()).hexdigest()
    source_paths = economic.REQUIRED_SOURCE_PIN_PATHS
    source_pins = {path: hashlib.sha256((economic.REPO / path).read_bytes()).hexdigest() for path in source_paths}
    protocol = economic.REPO / "data/research/crypto_max_return_novelty_2026_09/frozen_manifest.json"
    settings = economic.REPO / "config/settings.yaml"
    manifest = {
        "status": "FROZEN_NOVELTY",
        "manifest_sha256": "synthetic-cli-selected",
        "source": {"file_sha256": source_pins},
        "inputs": {"bars_1h": {"path": str(bars_path), "sha256": bars_hash}},
        "economic_provenance": {
            "protocol_path": str(protocol.relative_to(economic.REPO)),
            "protocol_sha256": hashlib.sha256(protocol.read_bytes()).hexdigest(),
            "settings_path": str(settings.relative_to(economic.REPO)),
            "settings_sha256": hashlib.sha256(settings.read_bytes()).hexdigest(),
        },
    }
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "receipt.json"
    output_path = tmp_path / "cli-output.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    receipt_path.write_text(json.dumps({"manifest_sha256": manifest["manifest_sha256"], "observed_sha256": {"bars_1h": bars_hash}}), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.research.crypto_max_return_economic_2026_09", "--run", "--manifest", str(manifest_path), "--receipt", str(receipt_path), "--output", str(output_path)],
        cwd=economic.REPO,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert output_path.is_file(), completed.stdout + completed.stderr
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert output["cell_count"] == 18
    assert len(output["cells"]) == 18
    assert output["manifest_sha256"] == manifest["manifest_sha256"]
    assert completed.returncode == 0, completed.stdout + completed.stderr
