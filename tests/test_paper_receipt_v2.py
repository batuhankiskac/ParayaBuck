"""One immutable two-market receipt identity for scheduled and catch-up decisions."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from parayabuck.core.models import AccountSnapshot, Market, PositionSnapshot
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide
from parayabuck.monitoring import receipt as receipt_module
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.portfolio.runtime_models import BuyAdmission, PortfolioRuntimeResult
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.portfolio.runtime_service import PortfolioRuntime
from parayabuck.runtime import EngineRuntime, RuntimeMode


def _payload(decision_id: str, market: str, origin: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "receipt_id": decision_id,
        "decision_id": decision_id,
        "decision_timestamp": "2026-09-24T00:00:00+00:00",
        "market": market,
        "origin": origin,
        "configuration_digest": "digest",
    }


def test_v2_filename_identity_and_immutable_retry(tmp_path: Path) -> None:
    paths = {
        receipt_module.write_decision_receipt_v2(_payload(f"d{index}", market, origin), tmp_path)
        for index, (market, origin) in enumerate(
            (
                ("equity", "scheduled"),
                ("equity", "startup_catch_up"),
                ("crypto", "scheduled"),
                ("crypto", "startup_catch_up"),
            )
        )
    }
    assert len(paths) == 4
    first = _payload("d0", "equity", "scheduled")
    first["capacity_snapshot"] = {"sampled_at": "later"}
    existing = receipt_module.write_decision_receipt_v2(first, tmp_path)
    assert json.loads(existing.read_text(encoding="utf-8"))["decision_id"] == "d0"
    assert len(list(tmp_path.glob("*.v2.json"))) == 4
    conflicting = _payload("d0", "equity", "scheduled")
    conflicting["configuration_digest"] = "other-config"
    with pytest.raises(ValueError, match="conflict"):
        receipt_module.write_decision_receipt_v2(conflicting, tmp_path)


@pytest.mark.parametrize("market", (Market.EQUITY, Market.CRYPTO))
@pytest.mark.parametrize("origin", ("scheduled", "startup_catch_up"))
def test_engine_emits_shared_receipt_for_both_markets_and_origins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, market: Market, origin: str
) -> None:
    monkeypatch.setattr(receipt_module, "default_receipt_dir", lambda: tmp_path)
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.mode = RuntimeMode.PAPER
    engine.app_config = SimpleNamespace(
        universe=SimpleNamespace(
            equity=SimpleNamespace(observation_strategy_parameters_digest="eq-observation"),
            crypto=SimpleNamespace(observation_strategy_parameters_digest="cr-observation"),
        )
    )
    engine.capacity_health = lambda: {
        "effective_global_slot_claims": 1,
        "global_slot_cap": 8,
        "effective_crypto_slot_claims": 1 if market == Market.CRYPTO else 0,
        "crypto_slot_cap": 2,
    }
    strategy = "equity_leveraged_trend_v1" if market == Market.EQUITY else "crypto_low_variance_hysteresis_v1"
    symbol = "AAPL" if market == Market.EQUITY else "BTC/USD"
    target = TargetPortfolio(
        strategy,
        "1.0",
        datetime(2026, 9, 24, tzinfo=UTC),
        (TargetWeight(symbol, 0.05),),
        0.95,
        universe_version="universe-version",
        metadata={"decision_origin": origin, "explanations": {symbol: {"alpha_rank": 1}}},
    )
    runtime = SimpleNamespace(
        market=market,
        maximum_allocation=0.55 if market == Market.EQUITY else 0.05,
        configuration_digest="config-digest",
        universe_version="universe-digest",
        symbols=(symbol,),
        last_buy_admission=None,
        repository=SimpleNamespace(orders_for_decision=lambda _: ()),
    )
    result = PortfolioRuntimeResult(
        f"decision{market.value}{origin.replace('_', '')}", target, None, (), (), "no_orders"
    )
    engine._record_paper_decision_receipt(runtime, result, None)
    files = list(tmp_path.glob("*.v2.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert (saved["market"], saved["origin"], saved["decision_id"]) == (market.value, origin, result.decision_id)
    assert saved["target_state"]["kind"] == "INTENDED_POST_RISK"
    assert saved["planned_orders"] == []
    assert saved["execution_confirmation"]["confirmed_positions_after"] is None


def test_receipt_failure_cannot_interrupt_trading_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(receipt_module, "default_receipt_dir", lambda: tmp_path)
    monkeypatch.setattr(receipt_module, "write_decision_receipt_v2", lambda *_: 1 / 0)
    from parayabuck.monitoring import alerts

    monkeypatch.setattr(alerts, "emit", lambda *_, **__: 1 / 0)
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.mode = RuntimeMode.PAPER
    engine.capacity_health = lambda: {"effective_global_slot_claims": 0, "effective_crypto_slot_claims": 0}
    engine.app_config = SimpleNamespace(
        universe=SimpleNamespace(
            equity=SimpleNamespace(observation_strategy_parameters_digest=""),
            crypto=SimpleNamespace(observation_strategy_parameters_digest=""),
        )
    )
    target = TargetPortfolio("equity_leveraged_trend_v1", "1.0.0", datetime(2026, 9, 24, tzinfo=UTC), (), 1.0)
    runtime = SimpleNamespace(
        market=Market.EQUITY,
        maximum_allocation=0.55,
        configuration_digest="config",
        universe_version="universe",
        symbols=(),
        last_buy_admission=None,
        repository=SimpleNamespace(orders_for_decision=lambda _: ()),
    )
    result = PortfolioRuntimeResult("decision1", target, None, (), (), "no_orders")
    engine._record_paper_decision_receipt(runtime, result, None)
    assert not list(tmp_path.glob("*.v2.json"))


def test_final_buy_observation_uses_actual_admitted_order_and_is_best_effort() -> None:
    account = AccountSnapshot(
        cash=50_000,
        equity=100_000,
        peak_equity=100_000,
        daily_pnl=0,
        positions=(PositionSnapshot("BTC/USD", 1, 100, 120, Market.CRYPTO, managed_qty=1),),
    )
    order = PortfolioOrderRequest(
        "buy-1",
        "decision-1",
        "BTC/USD",
        Market.CRYPTO,
        PortfolioOrderSide.BUY,
        2,
        100,
        datetime(2026, 9, 24, tzinfo=UTC),
        "crypto_low_variance_hysteresis_v1",
        "1.0",
        False,
    )
    admission = BuyAdmission(account, (order,), 120, 100, 1000, 1000, None)
    captured: list[tuple[object, ...]] = []
    runtime = PortfolioRuntime.__new__(PortfolioRuntime)
    runtime.mode = "paper"
    runtime.repository = SimpleNamespace(
        record_buy_admission_observation=lambda *args, **kwargs: captured.append((args, kwargs))
    )
    runtime._record_buy_admission_observation("decision-1", admission, (order,))
    assert captured[0][0] == ("decision-1", ("buy-1",))
    assert captured[0][1]["admitted_batch_notional"] == 240
    runtime.repository = SimpleNamespace(record_buy_admission_observation=lambda *args, **kwargs: 1 / 0)
    runtime._record_buy_admission_observation("decision-1", admission, (order,))


def test_final_buy_observation_is_durable_and_idempotent(tmp_path: Path) -> None:
    repository = PortfolioStateRepository(tmp_path / "portfolio.sqlite3")
    try:
        with repository.store.lock, repository.store.connection:
            repository.store.connection.execute(
                "INSERT INTO portfolio_runtime_sessions(session_id,account_id,configuration_digest,mode,started_at,status) VALUES ('s','a','c','paper','2026-09-24','running')"
            )
            repository.store.connection.execute(
                "INSERT INTO portfolio_decisions(decision_id,session_id,account_id,configuration_digest,decision_timestamp,market,strategy_name,target_json,risk_json) VALUES ('d','s','a','c','2026-09-24','crypto','crypto_low_variance_hysteresis_v1','{}','{}')"
            )
            repository.store.connection.execute(
                "INSERT INTO portfolio_orders(client_order_id,decision_id,symbol,market,side,quantity,reference_price,status,created_at) VALUES ('o','d','BTC/USD','crypto','buy',1,100,'submitting','2026-09-24')"
            )
        arguments = dict(
            account_equity=100_000,
            account_cash=50_000,
            realized_gross=1000,
            committed_buy_notional=100,
            gross_headroom=1000,
            cash_headroom=1000,
            admitted_batch_notional=100,
        )
        repository.record_buy_admission_observation("d", ("o",), **arguments)
        repository.record_buy_admission_observation("d", ("o",), **{**arguments, "admitted_batch_notional": 200})
        row = repository.store.connection.execute(
            "SELECT admitted_batch_notional FROM portfolio_buy_admission_observations WHERE client_order_id='o' ORDER BY observed_at, observation_id"
        ).fetchall()
        assert [item[0] for item in row] == [100, 200]
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("selected", "ranks", "retained", "held", "expected_previous"),
    [
        (("BTC/USD", "LTC/USD"), (1, 2), (), (), {}),
        (("BTC/USD", "LTC/USD"), (3, 1), ("BTC/USD",), ("BTC/USD",), {"crypto:BTC/USD": 1.0}),
        (("LTC/USD",), (1,), (), ("BTC/USD",), {"crypto:BTC/USD": 1.0}),
    ],
)
def test_crypto_receipt_preserves_rank_hysteresis_and_exiting_holdings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selected: tuple[str, ...],
    ranks: tuple[int, ...],
    retained: tuple[str, ...],
    held: tuple[str, ...],
    expected_previous: dict[str, float],
) -> None:
    monkeypatch.setattr(receipt_module, "default_receipt_dir", lambda: tmp_path)
    engine = EngineRuntime.__new__(EngineRuntime)
    engine.mode = RuntimeMode.PAPER
    engine.capacity_health = lambda: {
        "effective_global_slot_claims": len(held),
        "global_slot_cap": 8,
        "effective_crypto_slot_claims": len(held),
        "crypto_slot_cap": 2,
    }
    engine.app_config = SimpleNamespace(
        universe=SimpleNamespace(
            crypto=SimpleNamespace(observation_strategy_parameters_digest="observation-digest"),
        )
    )
    target = TargetPortfolio(
        "crypto_low_variance_hysteresis_v1",
        "1.0.0",
        datetime(2026, 9, 24, tzinfo=UTC),
        tuple(TargetWeight(symbol, 0.05 / len(selected)) for symbol in selected),
        0.95,
        metadata={
            "retained": retained,
            "explanations": {
                symbol: {"alpha_rank": rank, "retained": symbol in retained} for symbol, rank in zip(selected, ranks)
            },
        },
    )
    account = AccountSnapshot(
        cash=99_900,
        equity=100_000,
        peak_equity=100_000,
        daily_pnl=0,
        positions=tuple(PositionSnapshot(symbol, 1, 100, 100, Market.CRYPTO, managed_qty=1) for symbol in held),
    )
    runtime = SimpleNamespace(
        market=Market.CRYPTO,
        maximum_allocation=0.05,
        configuration_digest="config",
        universe_version="universe",
        symbols=selected,
        last_buy_admission=None,
        repository=SimpleNamespace(orders_for_decision=lambda _: ()),
    )
    result = PortfolioRuntimeResult("crypto123", target, None, (), (), "no_orders")
    engine._record_paper_decision_receipt(runtime, result, account)
    saved = json.loads(next(tmp_path.glob("*.v2.json")).read_text(encoding="utf-8"))
    assert saved["crypto_provenance"]["ranks"] == dict(zip(selected, ranks))
    assert saved["crypto_provenance"]["retained"] == {symbol: symbol in retained for symbol in selected}
    assert saved["crypto_provenance"]["previous_holdings"] == expected_previous


def test_startup_catch_up_dispatches_both_market_results_to_receipt_writer() -> None:
    engine = EngineRuntime.__new__(EngineRuntime)
    engine._running = True
    engine._warming_up = False
    engine._circuit_breaker_reason = None
    engine._reconcile_failed = False
    engine._portfolio_lock = threading.RLock()
    engine._entry_risk_latch = {Market.EQUITY: None, Market.CRYPTO: None}
    engine._entry_readiness_reasons = lambda _market: ()
    engine._crypto_first_portfolio_order = lambda runtimes: runtimes
    engine._ordered_buy_position_keys = lambda *_: ()
    results = [
        PortfolioRuntimeResult("crypto-catchup", None, None, (), (), "no_orders"),
        PortfolioRuntimeResult("equity-catchup", None, None, (), (), "no_orders"),
    ]
    runtimes = (
        SimpleNamespace(market=Market.CRYPTO, startup_catch_up=lambda **_: results[0]),
        SimpleNamespace(market=Market.EQUITY, history={}, startup_catch_up=lambda **_: results[1]),
    )
    engine._portfolio_runtimes = runtimes
    recorded: list[tuple[Market, str | None]] = []
    engine._record_paper_decision_receipt = lambda runtime, result, _account: recorded.append(
        (runtime.market, result.decision_id)
    )
    engine._run_startup_catch_up()
    assert recorded == [(Market.CRYPTO, "crypto-catchup"), (Market.EQUITY, "equity-catchup")]
