from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import csv
import math
import os
import subprocess
import sys

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.data.aggregator import aggregate_historical_bars
from parayabuck.portfolio.base import PortfolioContext

candidate = pytest.importorskip("scripts.research.crypto_max_return_novelty_2026_09")
runner = pytest.importorskip("scripts.research.crypto_max_return_novelty_runner_2026_09")
research_dir = str(runner.REPO / "scripts/research")
if research_dir not in sys.path:
    sys.path.insert(0, research_dir)


def _bar(symbol: str, timestamp: datetime, close: float, *, timeframe: str = "1d") -> Bar:
    return Bar(
        symbol=symbol,
        market=Market.CRYPTO,
        timeframe=timeframe,
        timestamp=timestamp,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000.0,
    )


def _series(symbol: str, returns: list[float], *, start: datetime | None = None) -> tuple[Bar, ...]:
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


def _context(histories: dict[str, tuple[Bar, ...]], members: set[str] | None = None) -> PortfolioContext:
    decision = max(series[-1].timestamp for series in histories.values())
    return PortfolioContext(
        decision_timestamp=decision,
        market=Market.CRYPTO,
        timeframe="1d",
        history=histories,
        universe=_Universe(members or set(histories)),
        universe_version="synthetic",
        equity=100_000.0,
        holdings={},
        prices={symbol: series[-1].close for symbol, series in histories.items()},
        benchmark_symbol="BTC/USD",
    )


def test_max_daily_return_is_not_cumulative_return_and_ascending() -> None:
    # A and B have the same 22-day compounded return, but B has one larger
    # daily spike. MAX must rank A first because its maximum daily return is lower.
    a_returns = [0.01, 0.01] + [0.0] * 20
    b_returns = [0.0201] + [0.0] * 21
    histories = {
        "A/USD": _series("A/USD", a_returns),
        "B/USD": _series("B/USD", b_returns),
    }
    scores = candidate.max_return_scores(_context(histories))
    assert [symbol for _score, symbol in scores] == ["A/USD", "B/USD"]
    assert scores[0][0] == pytest.approx(0.01)
    assert scores[1][0] == pytest.approx(0.0201)


def test_score_uses_only_completed_bars_at_decision() -> None:
    returns = [0.0] * 22
    base = list(_series("A/USD", returns))
    future = _bar("A/USD", base[-1].timestamp + timedelta(days=1), 10_000.0)
    histories = {"A/USD": tuple(base + [future]), "B/USD": _series("B/USD", returns)}
    context = _context(histories)
    assert context.decision_timestamp == future.timestamp
    # A caller-provided decision timestamp before the future bar must exclude it.
    context = context.__class__(
        context.decision_timestamp - timedelta(days=1),
        context.market,
        context.timeframe,
        context.history,
        context.universe,
        context.universe_version,
        context.equity,
        context.holdings,
        context.prices,
        context.benchmark_symbol,
    )
    scores = candidate.max_return_scores(context)
    score_by_symbol = {symbol: score for score, symbol in scores}
    assert score_by_symbol["A/USD"] == pytest.approx(0.0)


def test_missing_daily_bar_does_not_count_as_consecutive_history() -> None:
    returns = [0.0] * 22
    series = list(_series("A/USD", returns))
    # Replace one interior date with a duplicate timestamp: span remains 22
    # days, but the required 23 daily observations are not consecutive.
    series[10] = _bar("A/USD", series[9].timestamp, series[10].close)
    scores = candidate.max_return_scores(_context({"A/USD": tuple(series)}))
    assert scores == ()


def test_configured_pool_and_stream_cap_are_observation_membership() -> None:
    returns = [0.0] * 22
    histories = {f"C{i:02d}/USD": _series(f"C{i:02d}/USD", returns) for i in range(22)}
    observed = {f"C{i:02d}/USD" for i in range(12)}
    scores = candidate.max_return_scores(_context(histories, observed))
    assert len(scores) == 12
    assert {symbol for _score, symbol in scores} == observed


def test_nonfinite_and_nonpositive_prices_are_not_fake_zero_scores() -> None:
    returns = [0.0] * 22
    valid = list(_series("VALID/USD", returns))
    invalid = list(_series("BAD/USD", returns))
    # Bar normally rejects NaN at construction; emulate a malformed upstream
    # object without weakening the production model validation.
    malformed = object.__new__(Bar)
    object.__setattr__(malformed, "symbol", "BAD/USD")
    object.__setattr__(malformed, "market", Market.CRYPTO)
    object.__setattr__(malformed, "timeframe", "1d")
    object.__setattr__(malformed, "timestamp", invalid[-1].timestamp)
    for field, value in (("open", float("nan")), ("high", float("nan")), ("low", float("nan")), ("close", float("nan")), ("volume", 1000.0)):
        object.__setattr__(malformed, field, value)
    invalid[-1] = malformed
    zero = list(_series("ZERO/USD", returns))
    zero[-1] = _bar("ZERO/USD", zero[-1].timestamp, 0.0)
    scores = candidate.max_return_scores(_context({"VALID/USD": tuple(valid), "BAD/USD": tuple(invalid), "ZERO/USD": tuple(zero)}))
    assert [symbol for _score, symbol in scores] == ["VALID/USD"]


def test_incomplete_same_utc_day_is_not_a_completed_daily_close() -> None:
    start = datetime(2024, 1, 2, tzinfo=UTC)
    complete = [
        _bar("BTC/USD", start + timedelta(hours=i), 100.0 + i, timeframe="1h")
        for i in range(24)
    ]
    partial = [
        _bar("BTC/USD", start + timedelta(days=1, hours=i), 200.0 + i, timeframe="1h")
        for i in range(3)
    ]
    before_day_close = aggregate_historical_bars(complete + partial, "1d", require_complete=True)
    assert len(before_day_close) == 1
    assert before_day_close[0].close == pytest.approx(123.0)

    after_day_close = aggregate_historical_bars(complete + partial + [
        _bar("BTC/USD", start + timedelta(days=1, hours=i), 200.0 + i, timeframe="1h")
        for i in range(3, 24)
    ], "1d", require_complete=True)
    assert len(after_day_close) == 2
    assert after_day_close[0].close == before_day_close[0].close


def test_selected_manifest_and_receipt_hashes_are_explicit_and_fail_closed(tmp_path) -> None:
    source_path = "scripts/research/crypto_max_return_novelty_2026_09.py"
    source_hash = hashlib.sha256((runner.REPO / source_path).read_bytes()).hexdigest()
    manifest = {
        "status": "FROZEN_NOVELTY",
        "manifest_sha256": "selected-manifest",
        "source": {"file_sha256": {source_path: source_hash}},
        "inputs": {"bars": {"sha256": "bars-selected"}},
    }
    receipt = {"manifest_sha256": "selected-manifest", "observed_sha256": {"bars": "bars-selected"}}
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "receipt.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    assert runner.verify_selected_inputs(manifest_path, receipt_path)["manifest_sha256"] == "selected-manifest"

    receipt["manifest_sha256"] = "stale-manifest"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(RuntimeError, match="BLOCKED_RECEIPT_MANIFEST_MISMATCH"):
        runner.verify_selected_inputs(manifest_path, receipt_path)


def test_near_duplicate_boundaries_are_and_not_or(monkeypatch) -> None:
    traces = []
    incumbent = []
    for index in range(10):
        candidate_ranks = {"A/USD": 1, "B/USD": 2, "C/USD": 3}
        incumbent_ranks = candidate_ranks if index < 9 else {"A/USD": 2, "B/USD": 1, "C/USD": 3}
        traces.append({"ranks": candidate_ranks, "target_symbols": ("A/USD", "B/USD")})
        incumbent.append({"ranks": incumbent_ranks, "target_symbols": ("A/USD", "B/USD")})

    monkeypatch.setattr(runner, "rank_correlation", lambda _a, _b: 0.95)
    summary = runner.novelty_summary(traces, incumbent)
    assert summary["median_rank_correlation"] == pytest.approx(0.95)
    assert summary["identical_top2_fraction"] == pytest.approx(0.90)
    assert "AND" in summary["near_duplicate_rule"]


def test_native_novelty_vertical_smoke_uses_real_aggregation_and_scanner(tmp_path, monkeypatch) -> None:
    # The public runner must build the daily series and PIT scanner itself. No
    # backtest engine, fills, PnL or economic output is involved in this test.
    symbols = [
        "BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "DOGE/USD",
        "AVAX/USD", "LINK/USD", "LTC/USD", "BCH/USD", "DOT/USD", "UNI/USD",
        "AAVE/USD", "ARB/USD", "FIL/USD", "RENDER/USD", "ONDO/USD", "HYPE/USD",
        "SHIB/USD", "GRT/USD", "CRV/USD", "PEPE/USD",
    ]
    start = datetime(2024, 1, 1, tzinfo=UTC)
    bars_path = tmp_path / "crypto_fixture_1h.csv"
    with bars_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "symbol", "open", "high", "low", "close", "volume"))
        for day in range(46):
            if day == 5:
                # A missing completed UTC day must not shift the later 96h grid.
                continue
            for hour in range(24):
                timestamp = start + timedelta(days=day, hours=hour)
                for index, symbol in enumerate(symbols):
                    close = 100.0 + index + day * 0.01
                    writer.writerow((timestamp.isoformat().replace("+00:00", "Z"), symbol, close, close, close, close, 10_000.0))

    candidate_path = runner.REPO / "scripts/research/crypto_max_return_novelty_2026_09.py"
    runner_path = runner.REPO / "scripts/research/crypto_max_return_novelty_runner_2026_09.py"
    source_pins = {
        str(path.relative_to(runner.REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (candidate_path, runner_path)
    }
    bars_hash = hashlib.sha256(bars_path.read_bytes()).hexdigest()
    manifest = {
        "status": "FROZEN_NOVELTY",
        "manifest_sha256": "vertical-selected-manifest",
        "source": {"file_sha256": source_pins},
        "inputs": {"bars_1h": {"path": str(bars_path), "sha256": bars_hash}},
        "evaluation": {
            "full": {"start": "2024-01-01T00:00:00Z", "end": "2024-02-15T23:59:59Z"}
        },
    }
    receipt = {"manifest_sha256": "vertical-selected-manifest", "observed_sha256": {"bars_1h": bars_hash}}
    manifest_path = tmp_path / "manifest.json"
    receipt_path = tmp_path / "receipt.json"
    output_path = tmp_path / "novelty.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    research_dir = str(runner.REPO / "scripts/research")
    if research_dir not in sys.path:
        sys.path.insert(0, research_dir)
    captured: dict[str, object] = {}
    original_summary = runner.novelty_summary

    def capture_summary(candidate_traces, incumbent_traces):
        captured["candidate"] = candidate_traces
        captured["incumbent"] = incumbent_traces
        return original_summary(candidate_traces, incumbent_traces)

    monkeypatch.setattr(runner, "novelty_summary", capture_summary)
    result = runner.run_native_novelty(manifest_path, receipt_path, output_path=output_path)
    assert result["economic_run"] is False
    assert result["engine_run"] is False
    assert result["virtual_holdings_only"] is True
    assert result["observer_cap"] == 12
    assert result["decision_count"] > 0
    assert result["manifest_sha256"] == "vertical-selected-manifest"
    assert output_path.is_file()
    candidate_traces = captured["candidate"]
    incumbent_traces = captured["incumbent"]
    assert isinstance(candidate_traces, list) and isinstance(incumbent_traces, list)
    assert len(candidate_traces) == len(incumbent_traces) == result["decision_count"]
    timestamps = [trace["decision_timestamp"] for trace in candidate_traces]
    assert all(right - left == timedelta(days=4) for left, right in zip(timestamps, timestamps[1:]))
    scored_traces = []
    for candidate_trace, incumbent_trace in zip(candidate_traces, incumbent_traces, strict=True):
        assert candidate_trace["decision_timestamp"] == incumbent_trace["decision_timestamp"]
        assert len(candidate_trace["observer_symbols"]) <= 12
        assert set(candidate_trace["observer_symbols"]).issubset(candidate_trace["full_eligible_symbols"])
        assert "target_symbols" in candidate_trace
        if candidate_trace["candidate_scores"]:
            scored_traces.append(candidate_trace)
            assert candidate_trace["ranks"]
    assert scored_traces, "native scanner/replay produced no complete MAX score trace"

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(written["candidate_traces"]) == len(written["incumbent_traces"]) == written["paired_snapshot_count"]
    required_trace_fields = {
        "decision_timestamp", "candidate_scores", "ranks", "full_eligible_symbols",
        "observer_symbols", "previous_held", "retained", "target_symbols",
    }
    for trace in (*written["candidate_traces"], *written["incumbent_traces"]):
        assert required_trace_fields.issubset(trace), sorted(set(required_trace_fields) - set(trace))
        rank_symbols = set(trace["ranks"])
        assert set(trace["observer_symbols"]).issubset(rank_symbols)
        if trace["candidate_scores"]:
            assert set(trace["candidate_scores"]) == rank_symbols
    assert len(written["rows"]) == written["paired_snapshot_count"]
    for row, candidate_trace, incumbent_trace in zip(
        written["rows"], written["candidate_traces"], written["incumbent_traces"], strict=True
    ):
        common = sorted(set(candidate_trace["ranks"]) & set(incumbent_trace["ranks"]))
        if len(common) >= 2:
            candidate_values = [candidate_trace["ranks"][symbol] for symbol in common]
            incumbent_values = [incumbent_trace["ranks"][symbol] for symbol in common]
            candidate_mean = sum(candidate_values) / len(candidate_values)
            incumbent_mean = sum(incumbent_values) / len(incumbent_values)
            numerator = sum(
                (candidate_value - candidate_mean) * (incumbent_value - incumbent_mean)
                for candidate_value, incumbent_value in zip(candidate_values, incumbent_values, strict=True)
            )
            candidate_scale = math.sqrt(sum((value - candidate_mean) ** 2 for value in candidate_values))
            incumbent_scale = math.sqrt(sum((value - incumbent_mean) ** 2 for value in incumbent_values))
            expected_spearman = numerator / (candidate_scale * incumbent_scale) if candidate_scale and incumbent_scale else None
        else:
            expected_spearman = None
        assert row["spearman"] == expected_spearman or (
            row["spearman"] is not None and expected_spearman is not None and row["spearman"] == pytest.approx(expected_spearman)
        )
        candidate_top2 = tuple(sorted(candidate_trace["ranks"], key=candidate_trace["ranks"].get)[:2])
        incumbent_top2 = tuple(sorted(incumbent_trace["ranks"], key=incumbent_trace["ranks"].get)[:2])
        assert row["identical_top2"] == (candidate_top2 == incumbent_top2)
        candidate_targets = set(candidate_trace["target_symbols"])
        incumbent_targets = set(incumbent_trace["target_symbols"])
        union = candidate_targets | incumbent_targets
        expected_jaccard = len(candidate_targets & incumbent_targets) / len(union) if union else 1.0
        assert row["target_jaccard"] == pytest.approx(expected_jaccard)

    # Exercise the actual module CLI from the repository root without pytest's
    # scripts/research path injection. This remains novelty-only and uses the
    # same tiny temp fixture.
    subprocess_output = tmp_path / "novelty_subprocess.json"
    clean_env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    completed = subprocess.run(
        [
            sys.executable, "-m", "scripts.research.crypto_max_return_novelty_runner_2026_09",
            "--manifest", str(manifest_path), "--receipt", str(receipt_path), "--run", "--output", str(subprocess_output),
        ],
        cwd=runner.REPO,
        env=clean_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert subprocess_output.is_file()


def test_native_decision_is_prefix_invariant_to_unfinished_current_day(tmp_path, monkeypatch) -> None:
    symbols = [
        "BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "ADA/USD", "DOGE/USD",
        "AVAX/USD", "LINK/USD", "LTC/USD", "BCH/USD", "DOT/USD", "UNI/USD",
        "AAVE/USD", "ARB/USD", "FIL/USD", "RENDER/USD", "ONDO/USD", "HYPE/USD",
        "SHIB/USD", "GRT/USD", "CRV/USD", "PEPE/USD",
    ]
    start = datetime(2024, 1, 1, tzinfo=UTC)
    asof_day = 24

    def write_fixture(path, complete_asof_day: bool) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(("timestamp", "symbol", "open", "high", "low", "close", "volume"))
            for day in range(46):
                hours = range(24)
                if day == asof_day and not complete_asof_day:
                    hours = range(1)
                for hour in hours:
                    timestamp = start + timedelta(days=day, hours=hour)
                    for index, symbol in enumerate(symbols):
                        close = 100.0 + index + day * 0.01
                        writer.writerow((timestamp.isoformat().replace("+00:00", "Z"), symbol, close, close, close, close, 10_000.0))

    past_only = tmp_path / "past_only.csv"
    with_future_hours = tmp_path / "with_future_hours.csv"
    write_fixture(past_only, complete_asof_day=False)
    write_fixture(with_future_hours, complete_asof_day=True)

    candidate_path = runner.REPO / "scripts/research/crypto_max_return_novelty_2026_09.py"
    runner_path = runner.REPO / "scripts/research/crypto_max_return_novelty_runner_2026_09.py"
    source_pins = {
        str(path.relative_to(runner.REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (candidate_path, runner_path)
    }

    captured: list[tuple[list[dict[str, object]], list[dict[str, object]]]] = []
    original_summary = runner.novelty_summary

    def capture_summary(candidate_traces, incumbent_traces):
        captured.append((list(candidate_traces), list(incumbent_traces)))
        return original_summary(candidate_traces, incumbent_traces)

    monkeypatch.setattr(runner, "novelty_summary", capture_summary)
    for index, bars_path in enumerate((past_only, with_future_hours)):
        bars_hash = hashlib.sha256(bars_path.read_bytes()).hexdigest()
        manifest = {
            "status": "FROZEN_NOVELTY",
            "manifest_sha256": f"prefix-selected-{index}",
            "source": {"file_sha256": source_pins},
            "inputs": {"bars_1h": {"path": str(bars_path), "sha256": bars_hash}},
            "evaluation": {
                "full": {"start": "2024-01-25T00:00:00Z", "end": "2024-02-15T23:59:59Z"}
            },
        }
        receipt = {"manifest_sha256": f"prefix-selected-{index}", "observed_sha256": {"bars_1h": bars_hash}}
        manifest_path = tmp_path / f"prefix_manifest_{index}.json"
        receipt_path = tmp_path / f"prefix_receipt_{index}.json"
        output_path = tmp_path / f"prefix_output_{index}.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        runner.run_native_novelty(manifest_path, receipt_path, output_path=output_path)

    candidate_a, incumbent_a = captured[0]
    candidate_b, incumbent_b = captured[1]
    assert candidate_a and candidate_b
    assert len(candidate_a) == len(candidate_b)
    assert len(incumbent_a) == len(incumbent_b) == len(candidate_a)
    fields = ("decision_timestamp", "candidate_scores", "ranks", "eligible_symbols", "observed_symbols", "previous_held", "retained", "target_symbols")
    prefix_cutoff = datetime(2024, 1, 25, tzinfo=UTC)
    prefix_a = {trace["decision_timestamp"]: trace for trace in candidate_a if trace["decision_timestamp"] <= prefix_cutoff}
    prefix_b = {trace["decision_timestamp"]: trace for trace in candidate_b if trace["decision_timestamp"] <= prefix_cutoff}
    assert prefix_a, "native runner omitted the fixed-prefix decision entirely"
    assert set(prefix_a) == set(prefix_b)
    for timestamp in sorted(prefix_a):
        left, right = prefix_a[timestamp], prefix_b[timestamp]
        for field in fields:
            assert left[field] == right[field], f"prefix changed {field} at {timestamp}: {left[field]!r} != {right[field]!r}"
    incumbent_prefix_a = {trace["decision_timestamp"]: trace for trace in incumbent_a if trace["decision_timestamp"] <= prefix_cutoff}
    incumbent_prefix_b = {trace["decision_timestamp"]: trace for trace in incumbent_b if trace["decision_timestamp"] <= prefix_cutoff}
    assert incumbent_prefix_a and set(incumbent_prefix_a) == set(incumbent_prefix_b)
    for timestamp in sorted(incumbent_prefix_a):
        assert incumbent_prefix_a[timestamp] == incumbent_prefix_b[timestamp]
