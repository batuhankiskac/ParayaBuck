"""Focused diagnostic accounting and fail-closed parity contracts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from research.crypto_exit_giveback_audit_v2.canonical_replay import digest
from research.crypto_exit_giveback_audit_v2.episodes import reconstruct, summarize
from research.crypto_exit_giveback_audit_v2.parity import check
from research.crypto_exit_giveback_audit_v2.verify_episodes import verify_summary
from tests.test_crypto_giveback_v2_verifier import fixture


def test_partial_resize_completed_mark_path_and_threshold_attribution() -> None:
    raw, _ = fixture()
    raw["config"] = {"strategy": {"crypto": {"name": "test"}}}
    diag = reconstruct(raw)
    assert len(diag["episodes"]) == 1
    episode = diag["episodes"][0]
    assert episode["mfe"] == 147
    assert episode["final_pnl"] == 221
    assert episode["levels"]["0.4"]["ever_reached"]
    summary = summarize(diag["episodes"])
    assert verify_summary(diag["episodes"], summary)["status"] == "PASS"
    assert summary["right_tail"]["top1"]["share_positive_pnl"] == 1
    broken = deepcopy(summary)
    broken["right_tail"]["top1"]["pnl"] -= 1
    assert verify_summary(diag["episodes"], broken)["status"] == "FAIL"


def test_zero_target_parity_mutation_cannot_pass_by_keeping_counts() -> None:
    raw: dict[str, Any] = dict(
        decisions=[dict(target={}, orders=[]) for _ in range(455)],
        fills=[{} for _ in range(388)],
        final_state={},
        terminal_events=[],
        metrics={},
        data={},
        config={},
        universe_timeframe="1h",
        daily_bars=[],
        universe_hash="u",
    )
    baseline = dict(
        target_count=455,
        fill_count=388,
        target_hash=digest([d["target"] for d in raw["decisions"]]),
        decision_evidence_hash=digest(raw["decisions"]),
        fills_hash=digest(raw["fills"]),
        orders_hash=digest([d["orders"] for d in raw["decisions"]]),
        final_state_hash=digest(raw["final_state"]),
        terminal_hash=digest(raw["terminal_events"]),
        metrics={},
        payload_hash=digest(raw),
        input={},
        full_config={},
        universe_timeframe="1h",
        daily_bars_hash=digest([]),
        universe_hash="u",
        production_main_sha="synthetic",
    )
    assert check(raw, baseline)["status"] == "PASS"
    raw["decisions"][0]["target"] = {"weights": []}
    failed = check(raw, baseline)
    assert failed["status"] == "CRYPTO_GIVEBACK_AUDIT_INCONCLUSIVE_PARITY_FAILURE"
    assert "targets_hash" in failed["failed_checks"]
    assert failed["checks"]["455_decisions"]


def test_terminal_quote_timestamp_does_not_close_before_horizon_snapshot() -> None:
    raw, _ = fixture()
    raw["config"] = {"strategy": {"crypto": {"name": "test"}}}
    final = raw["fills"].pop()
    raw["terminal_events"] = [
        dict(
            symbol=final["symbol"],
            timestamp=raw["snapshots"][-2]["timestamp"],
            quantity=15.0,
            fill_price=25.0,
            fee=1.0,
            policy="FINAL_BAR",
        )
    ]
    raw["snapshots"][-1].update(
        nav=1222.0, positions={"AAA/USD": dict(qty=15.0, avg_price=15.0, entry_fee=1.5, mark=25.0)}
    )
    episode = reconstruct(raw)["episodes"][0]
    assert episode["censored"]
    assert episode["exit_timestamp"] == raw["snapshots"][-1]["timestamp"]
    assert episode["exit_event"]["timestamp"] == raw["terminal_events"][0]["timestamp"]
    assert episode["mfe"] == 222.0
    assert episode["mfe_to_exit_days"] == 0.0
