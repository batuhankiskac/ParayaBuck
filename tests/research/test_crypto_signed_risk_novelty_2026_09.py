from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.base import PortfolioContext

candidate = pytest.importorskip("scripts.research.crypto_signed_risk_novelty_2026_09")
runner = pytest.importorskip("scripts.research.crypto_signed_risk_novelty_runner_2026_09")


def _bar(symbol: str, timestamp: datetime, close: float, *, timeframe: str = "1d") -> Bar:
    return Bar(symbol=symbol, market=Market.CRYPTO, timeframe=timeframe, timestamp=timestamp,
               open=close, high=close, low=close, close=close, volume=10_000.0)


def _series(symbol: str, returns: list[float], start: datetime | None = None) -> tuple[Bar, ...]:
    start = start or datetime(2024, 1, 1, tzinfo=UTC)
    closes = [100.0]
    for value in returns:
        closes.append(closes[-1] * (1.0 + value))
    return tuple(_bar(symbol, start + timedelta(days=i), close) for i, close in enumerate(closes))


class _Universe:
    def __init__(self, members: set[str]) -> None:
        self.members = members

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return market == Market.CRYPTO and symbol in self.members


def _context(histories: dict[str, tuple[Bar, ...]], members: set[str] | None = None,
             decision: datetime | None = None) -> PortfolioContext:
    decision = decision or max(series[-1].timestamp for series in histories.values())
    return PortfolioContext(decision, Market.CRYPTO, "1d", histories,
                            _Universe(members or set(histories)), "synthetic", 100_000.0,
                            {}, {symbol: series[-1].close for symbol, series in histories.items()}, "BTC/USD")


def test_signed_risk_is_downside_energy_ratio_and_ascending() -> None:
    negative = [-0.02] + [0.01] * 21
    positive = [0.01] * 22
    scores = candidate.signed_risk_scores(_context({"NEG/USD": _series("NEG/USD", negative),
                                                      "POS/USD": _series("POS/USD", positive)}))
    values = dict((symbol, score) for score, symbol in scores)
    assert values["POS/USD"] == pytest.approx(0.0)
    assert values["NEG/USD"] > values["POS/USD"]
    assert [symbol for _score, symbol in scores] == ["POS/USD", "NEG/USD"]


def test_zero_energy_is_excluded_and_nonfinite_is_not_fake_zero() -> None:
    flat = list(_series("FLAT/USD", [0.0] * 22))
    malformed = list(_series("BAD/USD", [0.01] * 22))
    bad = object.__new__(Bar)
    for name, value in (("symbol", "BAD/USD"), ("market", Market.CRYPTO), ("timeframe", "1d"),
                        ("timestamp", malformed[-1].timestamp), ("open", float("nan")),
                        ("high", float("nan")), ("low", float("nan")), ("close", float("nan")),
                        ("volume", 1_000.0)):
        object.__setattr__(bad, name, value)
    malformed[-1] = bad
    valid = _series("VALID/USD", [0.01] * 22)
    scores = candidate.signed_risk_scores(_context({"FLAT/USD": tuple(flat), "BAD/USD": tuple(malformed),
                                                      "VALID/USD": valid}))
    assert [symbol for _score, symbol in scores] == ["VALID/USD"]


def test_missing_duplicate_future_and_nonconsecutive_days_are_rejected() -> None:
    values = list(_series("A/USD", [0.01] * 22))
    values[10] = _bar("A/USD", values[9].timestamp, values[10].close)
    future = list(_series("B/USD", [0.01] * 22))
    future.append(_bar("B/USD", future[-1].timestamp + timedelta(days=1), 10_000.0))
    decision = values[-1].timestamp
    scores = candidate.signed_risk_scores(_context({"A/USD": tuple(values), "B/USD": tuple(future)}, decision=decision))
    assert [symbol for _score, symbol in scores] == ["B/USD"]
    assert "A/USD" not in {symbol for _score, symbol in scores}


def test_same_variance_opposite_signs_and_same_total_different_tail_differ() -> None:
    a = [0.1, -0.1] * 11
    b = [-0.1, 0.1] * 11
    scores = dict((symbol, score) for score, symbol in candidate.signed_risk_scores(
        _context({"A/USD": _series("A/USD", a), "B/USD": _series("B/USD", b)})))
    assert scores["A/USD"] == pytest.approx(scores["B/USD"])
    concentrated = [-0.2] + [0.0] * 21
    dispersed = [-0.1, -0.1, 0.1] + [0.0] * 19
    values = dict((symbol, score) for score, symbol in candidate.signed_risk_scores(
        _context({"C/USD": _series("C/USD", concentrated), "D/USD": _series("D/USD", dispersed)})))
    assert values["C/USD"] != pytest.approx(values["D/USD"])


def test_spearman_constant_rank_vector_is_undefined_and_top2_is_a_set() -> None:
    assert runner.score_spearman({"A": 0.0, "B": 0.0}, {"A": 1.0, "B": 2.0}) is None
    candidate_trace = {"ranks": {"A": 1, "B": 2}, "candidate_scores": {"A": {"score": 0.1}, "B": {"score": 0.2}}}
    incumbent_trace = {"ranks": {"B": 1, "A": 2}, "candidate_scores": {"A": {"score": 0.1}, "B": {"score": 0.2}}}
    summary = runner.novelty_summary([candidate_trace], [incumbent_trace])
    assert summary["rows"][0]["identical_top2"] is True


def test_near_duplicate_gate_requires_both_controls_and_thirty_valid_pairs() -> None:
    traces = [{"ranks": {"A": 1, "B": 2, "C": 3}, "candidate_scores": {"A": {"score": 1.0}, "B": {"score": 2.0}, "C": {"score": 3.0}}, "target_symbols": ("A", "B")} for _ in range(30)]
    controls = [{"ranks": {"A": 1, "B": 2, "C": 3}, "candidate_scores": {"A": {"score": 1.0}, "B": {"score": 2.0}, "C": {"score": 3.0}}, "target_symbols": ("A", "B")} for _ in range(30)]
    summary = runner.novelty_summary(traces, controls)
    assert summary["valid_rank_correlation_count"] == 30
    assert summary["near_duplicate_status"] == "NO_ECONOMIC_RUN_NEAR_DUPLICATE"
    assert summary["economic_run"] is False


def test_selected_manifest_receipt_source_and_input_hashes_fail_closed(tmp_path) -> None:
    bars_path = tmp_path / "bars.csv"
    bars_path.write_text("timestamp,symbol,open,high,low,close,volume\n", encoding="utf-8")
    bars_hash = hashlib.sha256(bars_path.read_bytes()).hexdigest()
    source_pins = {
        relative: hashlib.sha256((runner.REPO / relative).read_bytes()).hexdigest()
        for relative in runner.REQUIRED_SOURCE_PATHS
    }
    manifest = {"status": "FROZEN_NOVELTY", "manifest_sha256": "selected",
                "source": {"file_sha256": source_pins},
                "inputs": {"bars_1h": {"path": str(bars_path), "sha256": bars_hash}},
                "economic_provenance": {"protocol_path": runner.REQUIRED_PROTOCOL_PATH,
                                         "protocol_sha256": runner.REQUIRED_PROTOCOL_SHA256,
                                         "settings_path": runner.REQUIRED_SETTINGS_PATH,
                                         "settings_sha256": hashlib.sha256((runner.REPO / runner.REQUIRED_SETTINGS_PATH).read_bytes()).hexdigest()}}
    receipt = {"manifest_sha256": "selected", "observed_sha256": {"bars_1h": bars_hash}}
    mp, rp = tmp_path / "manifest.json", tmp_path / "receipt.json"
    mp.write_text(json.dumps(manifest), encoding="utf-8")
    rp.write_text(json.dumps(receipt), encoding="utf-8")
    assert runner.verify_selected_inputs(mp, rp)["manifest_sha256"] == "selected"
    receipt["manifest_sha256"] = "stale"
    rp.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(RuntimeError, match="BLOCKED_RECEIPT_MANIFEST_MISMATCH"):
        runner.verify_selected_inputs(mp, rp)


def test_selected_input_content_hash_is_bound_to_the_receipt(tmp_path) -> None:
    bars_path = tmp_path / "bars.csv"
    bars_path.write_text("x", encoding="utf-8")
    source_pins = {relative: hashlib.sha256((runner.REPO / relative).read_bytes()).hexdigest()
                   for relative in runner.REQUIRED_SOURCE_PATHS}
    manifest = {"status": "FROZEN_NOVELTY", "manifest_sha256": "m", "source": {"file_sha256": source_pins},
                "inputs": {"bars_1h": {"path": str(bars_path), "sha256": "old"}},
                "economic_provenance": {"protocol_path": runner.REQUIRED_PROTOCOL_PATH,
                                         "protocol_sha256": runner.REQUIRED_PROTOCOL_SHA256,
                                         "settings_path": runner.REQUIRED_SETTINGS_PATH,
                                         "settings_sha256": hashlib.sha256((runner.REPO / runner.REQUIRED_SETTINGS_PATH).read_bytes()).hexdigest()}}
    receipt = {"manifest_sha256": "m", "observed_sha256": {"bars_1h": "old"}}
    mp, rp = tmp_path / "m.json", tmp_path / "r.json"
    mp.write_text(json.dumps(manifest), encoding="utf-8")
    rp.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(RuntimeError, match="BLOCKED_INPUT_HASH:bars_1h"):
        runner.verify_selected_inputs(mp, rp)


def test_external_temp_input_without_protocol_and_settings_provenance_is_blocked(tmp_path) -> None:
    bars_path = tmp_path / "external.csv"
    bars_path.write_text("synthetic", encoding="utf-8")
    source_pins = {relative: hashlib.sha256((runner.REPO / relative).read_bytes()).hexdigest()
                   for relative in runner.REQUIRED_SOURCE_PATHS}
    manifest = {"status": "FROZEN_NOVELTY", "manifest_sha256": "m", "source": {"file_sha256": source_pins},
                "inputs": {"bars_1h": {"path": str(bars_path), "sha256": hashlib.sha256(bars_path.read_bytes()).hexdigest()}}}
    receipt = {"manifest_sha256": "m", "observed_sha256": {"bars_1h": hashlib.sha256(bars_path.read_bytes()).hexdigest()}}
    mp, rp = tmp_path / "m.json", tmp_path / "r.json"
    mp.write_text(json.dumps(manifest), encoding="utf-8")
    rp.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(RuntimeError, match="BLOCKED_PROVENANCE_PINS_MISSING"):
        runner.verify_selected_inputs(mp, rp)


def test_native_signed_risk_pipeline_is_virtual_only_and_persists_trace(tmp_path, monkeypatch) -> None:
    # Real hourly loading, complete-day aggregation, scanner bootstrap, PIT
    # context construction and virtual strategy decisions; no engine/PnL.
    symbols = ["BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "DOGE/USD", "AVAX/USD", "LINK/USD",
               "LTC/USD", "BCH/USD", "DOT/USD", "UNI/USD", "AAVE/USD", "ARB/USD", "FIL/USD", "RENDER/USD",
               "ONDO/USD", "HYPE/USD", "SHIB/USD", "GRT/USD", "CRV/USD", "PEPE/USD"]
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars_path = tmp_path / "bars.csv"
    with bars_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "symbol", "open", "high", "low", "close", "volume"))
        for day in range(46):
            for hour in range(24):
                ts = start + timedelta(days=day, hours=hour)
                for index, symbol in enumerate(symbols):
                    close = 100 + index + day * 0.02 + hour * 0.001
                    writer.writerow((ts.isoformat().replace("+00:00", "Z"), symbol, close, close * 1.001, close * .999, close, 1_000_000.0))
    sources = {relative: hashlib.sha256((runner.REPO / relative).read_bytes()).hexdigest()
               for relative in runner.REQUIRED_SOURCE_PATHS}
    bars_hash = hashlib.sha256(bars_path.read_bytes()).hexdigest()
    manifest = {"status": "FROZEN_NOVELTY", "manifest_sha256": "vertical", "source": {"file_sha256": sources},
                "inputs": {"bars_1h": {"path": str(bars_path), "sha256": bars_hash}},
                "economic_provenance": {"protocol_path": runner.REQUIRED_PROTOCOL_PATH,
                                         "protocol_sha256": runner.REQUIRED_PROTOCOL_SHA256,
                                         "settings_path": runner.REQUIRED_SETTINGS_PATH,
                                         "settings_sha256": hashlib.sha256((runner.REPO / runner.REQUIRED_SETTINGS_PATH).read_bytes()).hexdigest()},
                "evaluation": {"full": {"start": "2024-01-25T00:00:00Z", "end": "2024-02-15T23:59:59Z"}}}
    receipt = {"manifest_sha256": "vertical", "observed_sha256": {"bars_1h": bars_hash}}
    mp, rp, out = tmp_path / "manifest.json", tmp_path / "receipt.json", tmp_path / "out.json"
    mp.write_text(json.dumps(manifest), encoding="utf-8")
    rp.write_text(json.dumps(receipt), encoding="utf-8")
    result = runner.run_native_novelty(mp, rp, output_path=out)
    assert result["economic_run"] is False and result["engine_run"] is False and result["virtual_holdings_only"] is True
    assert result["decision_count"] > 0 and out.is_file()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert len(written["candidate_traces"]) == len(written["incumbent_traces"]) == written["paired_snapshot_count"]
    for trace in written["candidate_traces"]:
        assert {"decision_timestamp", "candidate_scores", "ranks", "eligible_symbols", "observed_symbols",
                "previous_held", "retained", "target_symbols"}.issubset(trace)
        assert "full_raw_scores" in trace
        assert "max_diagnostic_scores" in trace
        assert "momentum_diagnostic_scores" in trace
        assert set(trace["candidate_scores"]).issubset(set(trace["full_raw_scores"]))
        assert set(trace["observer_symbols"]).issubset(set(trace["full_eligible_symbols"]))
    for trace in written["incumbent_traces"]:
        assert "full_raw_scores" in trace
        assert "t3_full_raw_scores" in trace
        assert set(trace["candidate_scores"]).issubset(set(trace["full_raw_scores"]))
    assert all(row["spearman"] is None or math.isfinite(row["spearman"])
               for row in written["rows"])

    # The same selected manifest must work through the clean module entrypoint,
    # without pytest's import path injection. This is still virtual replay only.
    cli_out = tmp_path / "cli-out.json"
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.research.crypto_signed_risk_novelty_runner_2026_09",
         "--manifest", str(mp), "--receipt", str(rp), "--run", "--output", str(cli_out)],
        cwd=runner.REPO, env=env, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert cli_out.is_file()
