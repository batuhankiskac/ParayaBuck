from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime

from research.abnormal_volume_economic_v1.verify_economic_v1 import (
    RawBar,
    _canonical_sha256,
    _verify_architecture_parity,
    _verify_costs,
    _verify_diagnostics,
    _verify_first_open_orders,
    _verify_run_hashes,
)


def _order_run() -> dict[str, object]:
    return {
        "decisions": [
            {
                "decision_timestamp": "2022-01-31T05:00:00+00:00",
                "target_weights": [["AAA", 0.1]],
                "pre_trade_weights": [],
                "equity_at_decision": 100.0,
            }
        ],
        "orders": [
            {
                "client_order_id": "order-1",
                "symbol": "AAA",
                "side": "buy",
                "decision_timestamp": "2022-01-31T05:00:00+00:00",
                "requested_qty": 1.0,
                "decision_reference_price": 10.0,
                "contract_decision_timestamp": "2022-01-31T21:00:00+00:00",
                "contract_fill_timestamp": "2022-02-01T14:30:00+00:00",
                "contract_fill_cycle_timestamp": "2022-02-01T05:00:00+00:00",
                "fill_session": "2022-02-01",
            }
        ],
        "fills": [
            {
                "client_order_id": "order-1",
                "symbol": "AAA",
                "side": "buy",
                "decision_timestamp": "2022-01-31T05:00:00+00:00",
                "timestamp": "2022-02-01T05:00:00+00:00",
                "qty": 0.4,
            }
        ],
        "first_open_expirations": [
            {
                "client_order_id": "order-1",
                "symbol": "AAA",
                "side": "buy",
                "decision_timestamp": "2022-01-31T05:00:00+00:00",
                "contract_fill_cycle_timestamp": "2022-02-01T05:00:00+00:00",
                "expired_qty": 0.6,
                "filled_qty": 0.4,
                "expiry_cycle_timestamp": "2022-02-01T05:00:00+00:00",
                "reason": "FIRST_OPEN_PARTIAL_EXPIRED",
            }
        ],
    }


def _order_bars() -> dict[str, dict[date, RawBar]]:
    bar = RawBar(
        timestamp=datetime.fromisoformat("2022-01-31T05:00:00+00:00"),
        session=date.fromisoformat("2022-01-31"),
        symbol="AAA",
        entity_id="entity-a",
        open=10.0,
        close=10.0,
        volume=1_000.0,
    )
    return {"AAA": {bar.session: bar}}


def test_independent_verifier_accepts_reconciled_first_open_partial_expiry() -> None:
    issues: list[str] = []
    _verify_first_open_orders("fixture", _order_run(), _order_bars(), issues)
    assert issues == []


def test_independent_verifier_rejects_later_session_fill() -> None:
    run = _order_run()
    run["fills"][0]["timestamp"] = "2022-02-02T05:00:00+00:00"  # type: ignore[index]
    issues: list[str] = []
    _verify_first_open_orders("fixture", run, _order_bars(), issues)
    assert any("outside the single contracted first-open cycle" in issue for issue in issues)


def test_independent_verifier_rejects_swapped_order_identity() -> None:
    run = _order_run()
    run["fills"][0]["side"] = "sell"  # type: ignore[index]
    issues: list[str] = []
    _verify_first_open_orders("fixture", run, _order_bars(), issues)
    assert any("fill/order side mismatch" in issue for issue in issues)


def test_independent_verifier_rejects_named_hash_tampering() -> None:
    run: dict[str, object] = {
        "decisions": [],
        "fills": [],
        "terminal_events": [],
        "nav_curve": [],
        "holdings_snapshots": [],
        "dividend_events": [],
        "orders": [],
        "first_open_expirations": [],
    }
    run["hashes"] = {
        f"{field}_sha256": _canonical_sha256(run[field])
        for field in (
            "decisions",
            "fills",
            "terminal_events",
            "nav_curve",
            "holdings_snapshots",
            "dividend_events",
            "orders",
            "first_open_expirations",
        )
    }
    run["run_sha256"] = _canonical_sha256(run)
    tampered = deepcopy(run)
    tampered["fills"] = [{"unexpected": True}]
    issues: list[str] = []
    _verify_run_hashes("fixture", tampered, issues)
    assert any("hash mismatch" in issue for issue in issues)


def test_independent_verifier_rejects_cost_mismatch() -> None:
    run = {
        "fills": [
            {
                "timestamp": "2022-02-01T05:00:00+00:00",
                "decision_timestamp": "2022-01-31T05:00:00+00:00",
                "fill_session": "2022-02-01",
                "symbol": "AAA",
                "side": "buy",
                "qty": 1.0,
                "reference_price": 100.0,
                "fill_price": 100.03,
                "slippage_cost": 0.0,
                "spread_cost": 0.01,
                "fee": 0.0,
            }
        ],
        "terminal_events": [],
    }
    issues: list[str] = []
    _verify_costs(run, 1.0, issues)
    assert any("slippage" in issue for issue in issues)


def test_independent_verifier_fails_closed_without_architecture_evidence() -> None:
    issues: list[str] = []
    result = _verify_architecture_parity({}, issues)
    assert not result["all_pass"]
    assert issues


def test_independent_verifier_rejects_incomplete_entity_alias_omission() -> None:
    baseline = {
        "target_weights": [["AAA", 0.1], ["AAB", 0.1], ["BBB", 0.1]],
        "decision_session": "2022-01-31",
        "contract_decision_timestamp": "2022-01-31T21:00:00+00:00",
        "fill_session": "2022-02-01",
        "contract_fill_timestamp": "2022-02-01T14:30:00+00:00",
    }
    loo = {**baseline, "target_weights": [["AAB", 0.1], ["BBB", 0.1]]}
    candidate_results = {
        "cost_scenarios": {
            "1.0": {
                "summary": {"concentration": {"largest_absolute_contribution_share": 0.2}},
                "decisions": [baseline],
            }
        },
        "leave_one_dominant_name_out": {
            "omitted_entity_id": "entity-a",
            "omitted_symbols": ["AAA"],
            "decisions": [loo],
            "fills": [],
            "nav_curve": [],
            "metrics": {},
        },
    }
    ledger = {
        "1.0": {
            "largest_absolute_contribution_share": 0.2,
            "largest_positive_entity": "entity-a",
            "largest_positive_entity_symbols": ["AAA", "AAB"],
        }
    }
    issues: list[str] = []
    _verify_diagnostics(candidate_results, ledger, issues)
    assert any("alias set" in issue for issue in issues)
