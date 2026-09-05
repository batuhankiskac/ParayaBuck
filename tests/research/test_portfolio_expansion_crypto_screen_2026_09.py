from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.core.models import Bar, Market
from parayabuck.portfolio.base import PortfolioContext

screen = pytest.importorskip("scripts.research.portfolio_expansion_crypto_screen_2026_09")


def bar(symbol: str, day: int, close: float, *, volume: float = 1000.0) -> Bar:
    ts = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=day)
    return Bar(symbol=symbol, market=Market.CRYPTO, timeframe="1d",
               timestamp=ts, open=close, high=close, low=close,
               close=close, volume=volume)


class Universe:
    def __init__(self, members: set[str]) -> None:
        self.members = members

    def contains(self, symbol: str, market: Market, timestamp: datetime) -> bool:
        return market == Market.CRYPTO and symbol in self.members


def context(histories: dict[str, tuple[Bar, ...]], *, decision: datetime | None = None,
            members: set[str] | None = None) -> PortfolioContext:
    last = max(b.timestamp for values in histories.values() for b in values)
    decision = decision or last
    return PortfolioContext(
        decision_timestamp=decision, market=Market.CRYPTO, timeframe="1d",
        history=histories, universe=Universe(members or set(histories)),
        universe_version="synthetic", equity=100_000.0, holdings={},
        prices={symbol: values[-1].close for symbol, values in histories.items()},
        benchmark_symbol="BTC/USD",
    )


def series(symbol: str, length: int, close_fn=lambda i: 100.0,
           *, missing_day: int | None = None) -> tuple[Bar, ...]:
    return tuple(
        bar(symbol, i, close_fn(i))
        for i in range(length)
        if i != missing_day
    )


def test_matrix_contract_is_exactly_seven_arms_two_windows_three_costs() -> None:
    assert screen.ARMS == (
        "low_variance22", "low_max22", "signed_risk22", "downside22",
        "reversal70", "high_proximity364", "low_disagreement5_45",
    )
    assert screen.WINDOWS["full"][0] == datetime(2022, 1, 1, tzinfo=UTC)
    assert screen.WINDOWS["recent"][0] == datetime(2024, 1, 1, tzinfo=UTC)
    assert screen.COST_MULTIPLIERS == (1.0, 2.0, 3.0)


@pytest.mark.parametrize("arm", screen.ARMS)
def test_all_adapters_freeze_common_policy(arm: str) -> None:
    strategy = screen.build_strategy(arm)
    assert strategy.parameters["top_n"] == 2
    assert strategy.parameters["rebalance_hours"] == 96
    assert strategy.rebalance_policy_declared == "hours:96"


def test_score_window_is_causal_when_future_bar_is_appended() -> None:
    values = {"A/USD": series("A/USD", 23, lambda i: 100.0 + i)}
    cutoff = datetime(2024, 1, 22, tzinfo=UTC)
    before = context(values, decision=cutoff)
    after_values = {
        "A/USD": values["A/USD"] + (bar("A/USD", 23, 50_000.0),)
    }
    after = context(after_values, decision=cutoff)
    strategy = screen.build_strategy("low_max22")
    assert strategy._score_candidates(before) == strategy._score_candidates(after)


def test_interior_missing_day_is_ineligible_for_daily_score() -> None:
    histories = {"A/USD": series("A/USD", 23, missing_day=11)}
    strategy = screen.build_strategy("downside22")
    assert strategy._score_candidates(context(histories)) == ()


def test_invalid_prices_and_zero_volume_do_not_become_fake_scores() -> None:
    histories = {
        "BAD/USD": tuple(bar("BAD/USD", i, 0.0) if i == 22 else bar("BAD/USD", i, 100.0)
                         for i in range(23)),
        "ZERO/USD": tuple(bar("ZERO/USD", i, 100.0, volume=0.0) for i in range(50)),
    }
    max_scores = screen.build_strategy("low_max22")._score_candidates(context(histories))
    assert [symbol for _score, symbol in max_scores] == ["ZERO/USD"]
    assert screen.build_strategy("low_disagreement5_45")._score_candidates(context(histories)) == ()


def test_reversal_requires_negative_71_price_window() -> None:
    histories = {
        "UP/USD": series("UP/USD", 71, lambda i: 100.0 + i),
        "DOWN/USD": series("DOWN/USD", 71, lambda i: 200.0 - i),
        "FLAT/USD": series("FLAT/USD", 71),
    }
    scores = screen.build_strategy("reversal70")._score_candidates(context(histories))
    assert [symbol for _score, symbol in scores] == ["DOWN/USD"]
    assert scores[0][0] < 0.0


def test_high_proximity_requires_full_364_price_warmup() -> None:
    short = {"A/USD": series("A/USD", 363)}
    full = {"A/USD": series("A/USD", 364)}
    strategy = screen.build_strategy("high_proximity364")
    assert strategy._score_candidates(context(short)) == ()
    assert len(strategy._score_candidates(context(full))) == 1


def test_score_ties_have_stable_symbol_order_and_universe_exclusion() -> None:
    histories = {
        "B/USD": series("B/USD", 23),
        "A/USD": series("A/USD", 23),
        "SPY/USD": series("SPY/USD", 23),
    }
    strategy = screen.build_strategy("low_max22")
    scores = strategy._score_candidates(context(histories, members={"A/USD", "B/USD"}))
    assert [symbol for _score, symbol in scores] == ["A/USD", "B/USD"]


def test_manifest_guard_fails_before_reading_input_hash(tmp_path: Path) -> None:
    manifest_path = tmp_path / "tmp_screen_missing_manifest.json"
    manifest_path.write_text(json.dumps({
        "status": "FROZEN_SCREEN_PROTOCOL",
        "source": {"file_sha256": {}},
        "inputs": {"bars_1h": {"path": "does-not-exist.csv", "sha256": "bad"}},
        "protocol": {"path": screen.PROTOCOL_PATH, "sha256": "bad"},
        "settings": {"path": screen.SETTINGS_PATH, "sha256": "bad"},
        "matrix": {"arms": list(screen.ARMS), "windows": list(screen.WINDOWS),
                   "cost_multipliers": list(screen.COST_MULTIPLIERS),
                   "required_unique_cells": 42},
    }))
    try:
        with pytest.raises(RuntimeError, match="BLOCKED_SOURCE_HASH"):
            screen.validate_manifest(manifest_path)
    finally:
        manifest_path.unlink(missing_ok=True)



def test_shards_are_disjoint_and_cover_exact_matrix() -> None:
    full = screen.canonical_cell_ids()
    shards = [screen.assigned_cell_ids(i, 4) for i in range(4)]
    assert len(full) == 42
    assert set().union(*map(set, shards)) == set(full)
    assert sum(map(len, shards)) == len(full)
    assert not (set(shards[0]) & set(shards[1]))
    with pytest.raises(ValueError, match="invalid shard"):
        screen.assigned_cell_ids(4, 4)


def test_existing_output_is_refused_before_data_loading(tmp_path, monkeypatch) -> None:
    output = tmp_path / "existing.json"
    output.write_text("sentinel")
    monkeypatch.setattr(screen, "validate_manifest", lambda _path, **_kwargs: {"manifest_sha256": "synthetic"})
    with pytest.raises(RuntimeError, match="BLOCKED_OUTPUT_EXISTS"):
        screen.run_matrix(tmp_path / "manifest.json", output)
    assert output.read_text() == "sentinel"


def test_shard_checkpoint_is_atomic_and_receipt_records_assignment(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(screen, "validate_manifest", lambda _path, **_kwargs: {"manifest_sha256": "synthetic", "inputs": {"bars_1h": {"path": "synthetic.csv"}}})
    monkeypatch.setattr(screen, "load_config", lambda _path: object())
    monkeypatch.setattr(screen, "load_csv_bars", lambda _path, default_market: [])
    monkeypatch.setattr(screen, "run_case", lambda *args, **kwargs: object())
    monkeypatch.setattr(screen, "case_payload", lambda _case: {"metrics": {"total_return": 0.0}})
    output = tmp_path / "shard.json"
    checkpoint_dir = tmp_path / "checkpoints"
    receipt = tmp_path / "invocation.json"
    payload = screen.run_matrix(tmp_path / "manifest.json", output, shard_index=1, shard_count=4,
                                checkpoint_dir=checkpoint_dir, invocation_receipt=receipt)
    assigned = screen.assigned_cell_ids(1, 4)
    assert payload["assigned_cell_ids"] == assigned
    assert payload["cell_count"] == len(assigned)
    assert len(list(checkpoint_dir.glob("*.json"))) == len(assigned)
    assert not list(checkpoint_dir.glob("*.tmp"))
    receipt_payload = json.loads(receipt.read_text())
    assert receipt_payload["assigned_cell_ids"] == list(assigned)
    assert receipt_payload["completed_cell_count"] == len(assigned)


def test_placeholder_manifest_digest_is_rejected_before_run(monkeypatch, tmp_path) -> None:
    draft = Path("data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_draft.json")
    manifest = json.loads(draft.read_text())
    # Make every pinned file check pass so this isolates the self-digest guard.
    expected = dict(manifest["source"]["file_sha256"])
    expected[str(manifest["inputs"]["bars_1h"]["path"])] = manifest["inputs"]["bars_1h"]["sha256"]
    expected[manifest["protocol"]["path"]] = manifest["protocol"]["sha256"]
    expected[manifest["settings"]["path"]] = manifest["settings"]["sha256"]
    monkeypatch.setattr(screen, "sha256", lambda path: expected[str(path.relative_to(screen.REPO))])
    with pytest.raises(RuntimeError, match="BLOCKED_MANIFEST_DIGEST_PLACEHOLDER"):
        screen.validate_manifest(draft, require_canonical=True)


def test_native_cli_run_uses_real_fixture_and_records_fills(tmp_path: Path) -> None:
    """Exercise one canonical cell through CLI, aggregation, and native engine."""
    fixture = tmp_path / "crypto_fixture_1h.csv"
    start = datetime(2021, 12, 1, tzinfo=UTC)
    end = datetime(2022, 1, 5, 23, tzinfo=UTC)
    rows = ["timestamp,symbol,open,high,low,close,volume"]
    timestamp = start
    while timestamp <= end:
        day = (timestamp.date() - start.date()).days
        hour = timestamp.hour
        for symbol, base in (("BTC/USD", 100.0), ("ETH/USD", 80.0)):
            close = base + day * 0.20 + (hour % 6) * 0.15
            open_price = close - 0.05
            rows.append(
                f"{timestamp.isoformat().replace('+00:00', 'Z')},{symbol},{open_price:.6f},"
                f"{close + 0.1:.6f},{open_price - 0.1:.6f},{close:.6f},1000"
            )
        timestamp += timedelta(hours=1)
    fixture.write_text("\n".join(rows) + "\n", encoding="utf-8")

    draft = json.loads(Path("data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_draft.json").read_text())
    draft["source"] = {"file_sha256": {
        path: hashlib.sha256((screen.REPO / path).read_bytes()).hexdigest()
        for path in screen.SOURCE_PINS
    }}
    manifest = dict(draft)
    manifest["manifest_sha256"] = None
    manifest["source"] = {"file_sha256": {
        path: hashlib.sha256((screen.REPO / path).read_bytes()).hexdigest()
        for path in screen.SOURCE_PINS
    }}
    manifest["inputs"] = {"bars_1h": {"path": str(fixture), "sha256": hashlib.sha256(fixture.read_bytes()).hexdigest()}}
    manifest["manifest_sha256"] = screen.canonical_manifest_digest(manifest)
    manifest_path = tmp_path / "fixture_manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    output = tmp_path / "native_output.json"
    receipt = tmp_path / "native_invocation.json"
    checkpoint_dir = tmp_path / "checkpoints"
    completed = subprocess.run(
        [sys.executable, str(Path(screen.__file__).resolve()), "--manifest", str(manifest_path), "--run",
         "--output", str(output), "--shard-index", "0", "--shard-count", "42",
         "--checkpoint-dir", str(checkpoint_dir), "--invocation-receipt", str(receipt)],
        cwd=screen.REPO, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["assigned_cell_ids"] == ["full/low_variance22/1x"]
    assert payload["cell_count"] == 1
    cell = payload["cells"][0]
    assert cell["window"] == "full"
    assert cell["fees"]["fee_total"] > 0.0
    assert cell["fills"]
    assert any(fill["side"] == "buy" for fill in cell["fills"])
    ledger = cell["cash_ledger"]
    assert ledger
    assert ledger[-1]["positions"]
    assert ledger[-1]["cash"] + sum(item["value"] for item in ledger[-1]["positions"].values()) == pytest.approx(
        ledger[-1]["equity"], rel=1e-10
    )
    assert json.loads(receipt.read_text(encoding="utf-8"))["completed_cell_count"] == 1
    assert len(list(checkpoint_dir.glob("*.json"))) == 1


@pytest.mark.parametrize("pin_group,pin_name", [
    ("source", "scripts/research/portfolio_expansion_crypto_screen_2026_09.py"),
    ("protocol", "path"),
    ("settings", "path"),
])
@pytest.mark.parametrize("mutation", ["missing", "mismatch"])
def test_required_source_protocol_settings_pins_fail_closed_independently(
    tmp_path: Path, pin_group: str, pin_name: str, mutation: str
) -> None:
    draft = json.loads(Path("data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_draft.json").read_text())
    draft["source"] = {"file_sha256": {
        path: hashlib.sha256((screen.REPO / path).read_bytes()).hexdigest()
        for path in screen.SOURCE_PINS
    }}
    if pin_group == "source":
        pins = draft["source"]["file_sha256"]
        if mutation == "missing":
            pins.pop(pin_name)
        else:
            pins[pin_name] = "0" * 64
        expected = f"BLOCKED_SOURCE_HASH:{pin_name}"
    else:
        row = draft[pin_group]
        if mutation == "missing":
            row.pop(pin_name)
        else:
            row[pin_name] = "wrong/path" if pin_name == "path" else "0" * 64
        expected = f"BLOCKED_{pin_group.upper()}_HASH"
    path = tmp_path / f"{pin_group}_{mutation}.json"
    path.write_text(json.dumps(draft), encoding="utf-8")
    with pytest.raises(RuntimeError, match=expected):
        screen.validate_manifest(path)


def test_canonical_manifest_digest_excludes_only_own_field() -> None:
    draft = json.loads(Path("data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_draft.json").read_text())
    digest = screen.canonical_manifest_digest(draft)
    changed = dict(draft)
    changed["manifest_sha256"] = "different"
    assert screen.canonical_manifest_digest(changed) == digest


def test_new_adapters_call_native_score_methods_with_known_finite_values() -> None:
    histories = {
        "BTC/USD": series("BTC/USD", 364, lambda i: 100.0 + i * 0.1),
        "ETH/USD": series("ETH/USD", 364, lambda i: 500.0 - i),
    }
    expected_downside = 0.006752518982626972
    downside = screen.build_strategy("downside22")._score_candidates(context(histories))
    assert downside[0][1] == "BTC/USD" or downside[0][1] == "ETH/USD"
    assert any(score == pytest.approx(expected_downside) for score, symbol in downside if symbol == "ETH/USD")
    for arm in ("downside22", "reversal70", "high_proximity364", "low_disagreement5_45"):
        scores = screen.build_strategy(arm)._score_candidates(context(histories))
        assert scores
        assert all(score == pytest.approx(float(score)) for score, _symbol in scores)
