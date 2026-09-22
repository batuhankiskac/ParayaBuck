"""Small independent synthetic fixtures; no runtime state or network."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from research.crypto_exit_giveback_audit_v2.verify_episodes import verify


def fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    times = [f"2024-01-0{i}T00:00:00+00:00" for i in range(1, 5)]
    fills = [
        dict(timestamp=t, symbol="AAA/USD", side=side, qty=q, fill_price=p, fee=1.0)
        for t, side, q, p in zip(
            times, ("buy", "buy", "sell", "sell"), (10.0, 10.0, 5.0, 15.0), (10.0, 20.0, 30.0, 25.0)
        )
    ]
    path = [
        dict(timestamp=t, pnl=pnl, realized=r, unrealized=u, qty=q, mark=m)
        for t, pnl, r, u, q, m in zip(
            times[:3], (19.0, 58.0, 147.0), (0.0, 0.0, 73.5), (19.0, 58.0, 73.5), (10.0, 20.0, 15.0), (12.0, 18.0, 20.0)
        )
    ]
    # After partial sale:149 proceeds -75.5 released basis =73.5 realized.
    snapshots = [
        dict(timestamp=t, nav=1000 + pnl, positions={"AAA/USD": dict(qty=q, avg_price=avg, entry_fee=fee, mark=m)})
        for t, pnl, q, avg, fee, m in zip(
            times[:3], (19.0, 58.0, 147.0), (10.0, 20.0, 15.0), (10.0, 15.0, 15.0), (1.0, 2.0, 1.5), (12.0, 18.0, 20.0)
        )
    ]
    snapshots.append(dict(timestamp=times[3], nav=1221.0, positions={}))
    raw = dict(
        decisions=[],
        fills=fills,
        snapshots=snapshots,
        terminal_events=[],
        metrics=dict(starting_equity=1000.0, ending_equity=1221.0),
    )
    ep = dict(
        episode_id="test:AAA/USD:1",
        symbol="AAA/USD",
        entry_timestamp=times[0],
        exit_timestamp=times[3],
        censored=False,
        capital=302.0,
        final_pnl=221.0,
        final_return=221 / 302,
        mfe=147.0,
        mae=19.0,
        mfe_return=147 / 302,
        mae_return=19 / 302,
        mfe_timestamp=times[2],
        mae_timestamp=times[0],
        giveback=-74.0,
        retention=221 / 147,
        holding_days=3.0,
        time_to_mfe_days=2.0,
        mfe_to_exit_days=1.0,
        fills=[{**f, "type": "fill"} for f in fills],
        path=path,
        hysteresis=dict(
            decision_path=[],
            mfe_rank_asof=None,
            mfe_rank_is_contemporaneous_decision=False,
            after_peak_decision_states=[],
            exit_reason="NO_EXIT_DECISION",
            exit_final_fill_decision=None,
            exit_initiating_decision=None,
            holding_age_lock_hours=None,
            holding_age_expiry_timestamp=None,
        ),
    )
    return raw, dict(strategy="test", episodes=[ep])


def test_partial_resize_mark_only_extrema_allows_retention_above_one() -> None:
    raw, diag = fixture()
    result = verify(raw, diag)
    assert result["status"] == "PASS", result["failures"]
    assert result["completed_marks_checked"] == 3
    assert diag["episodes"][0]["retention"] > 1


def test_verifier_detects_corrupt_partial_basis_path_and_double_charged_costs() -> None:
    raw, diag = fixture()
    broken = deepcopy(diag)
    broken["episodes"][0]["path"][2]["realized"] -= 1
    broken["episodes"][0]["final_pnl"] -= 1
    result = verify(raw, broken)
    assert result["status"] == "FAIL"
    assert any("realized" in reason for reason in result["failures"])
    assert any("final_pnl" in reason for reason in result["failures"])


def test_verifier_rejects_exit_settlement_used_as_mfe() -> None:
    raw, diag = fixture()
    diag["episodes"][0]["mfe"] = 221.0
    result = verify(raw, diag)
    assert result["status"] == "FAIL"
    assert any(".mfe:" in reason for reason in result["failures"])


def test_verifier_detects_invented_hysteresis_exit_reason() -> None:
    raw, diag = fixture()
    diag["episodes"][0]["hysteresis"]["exit_reason"] = "RANK_ABOVE_RETENTION"
    result = verify(raw, diag)
    assert result["status"] == "FAIL"
    assert any("hysteresis.exit_reason" in reason for reason in result["failures"])
