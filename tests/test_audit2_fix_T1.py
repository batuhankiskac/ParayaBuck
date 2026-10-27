"""Audit wave 2 test gaps (Q2-1, Q2-3): pins for fix-wave conditions that had no test.

Each test pins one invariant named by a Q2-* finding in docs/audit/raw-r7.md.
Everything runs on tmp_path or in memory: no network, no credentials, no
production paths.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import parayabuck.backup as backup
import parayabuck.monitoring.alerts as alerts_module
import parayabuck.monitoring.email_alerts as email_alerts_module
import parayabuck.runtime as runtime_module
from parayabuck import load_config
from parayabuck.config import DEFAULT_DECISION_DB_PATH
from parayabuck.core.models import Market
from parayabuck.execution.alpaca_account import (
    _FLAT_BROKER_ALLOWED,
    QUANTITY_MATCH_FLOOR,
    flat_broker_reconcile_scope,
    quantities_agree,
)
from parayabuck.execution.alpaca_reconciliation import ReconciliationService
from parayabuck.execution.portfolio import PortfolioOrderSide
from parayabuck.live_safety import operator_halt_marker_path, write_operator_halt
from parayabuck.portfolio.runtime_repository import PortfolioStateRepository
from parayabuck.portfolio.runtime_service import PortfolioRuntime
from parayabuck.runtime import EngineRuntime, TradingState
from tests.test_audit2_fix_B2 import T0, _decision, _fill, _record_sell, _runtime
from tests.test_audit_fix_W09 import _live_cfg, _manager
from tests.test_audit_fix_W14 import _epoch_setup, _Repository, _ServiceBroker, _sidecar

_REPO = Path(__file__).resolve().parents[1]
_SHIPPED = _REPO / "config" / "settings.yaml"
_PEAK = '{"peak_equity": 1234.5, "epoch": 2, "account_id": "acct-1"}'


# --------------------------------------------------------------------------- #
# Q2-3-1: the BUY gate of a rebalance advance admits a BUY only while ACTIVE
# --------------------------------------------------------------------------- #


def _buy_gate_runtime(monkeypatch: pytest.MonkeyPatch, **flags: Any) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._running = True
    runtime._circuit_breaker_reason = None
    runtime._warming_up = False
    runtime._reconcile_failed = False
    runtime._reconcile_pending_reason = None
    runtime._entry_risk_latch = {}
    runtime._market_entry_halts = {}
    for name, value in flags.items():
        setattr(runtime, name, value)
    monkeypatch.setattr(runtime, "_operator_halt_held", lambda: False)
    monkeypatch.setattr(runtime, "_buy_held_by_signal_basis", lambda market: False)
    return runtime


@pytest.mark.parametrize(
    ("flags", "state"),
    [
        pytest.param({}, TradingState.ACTIVE, id="active"),
        pytest.param({"_reconcile_failed": True}, TradingState.REDUCING, id="reconcile-failed"),
        pytest.param({"_reconcile_pending_reason": "pending"}, TradingState.REDUCING, id="reconcile-pending"),
        pytest.param(
            {"_entry_risk_latch": {Market.EQUITY: "daily_loss_limit"}}, TradingState.REDUCING, id="entry-latch"
        ),
        pytest.param({"_market_entry_halts": {Market.EQUITY: "halt"}}, TradingState.REDUCING, id="market-halt"),
        pytest.param({"_warming_up": True}, TradingState.HALTED, id="warming-up"),
    ],
)
def test_buy_gate_admits_a_buy_only_while_the_market_is_active(
    monkeypatch: pytest.MonkeyPatch, flags: dict[str, Any], state: TradingState
) -> None:
    runtime = _buy_gate_runtime(monkeypatch, **flags)
    buy = SimpleNamespace(side=PortfolioOrderSide.BUY)
    sell = SimpleNamespace(side=PortfolioOrderSide.SELL)

    assert runtime.trading_state(Market.EQUITY) is state
    reason = runtime._advance_submit_block_reason(Market.EQUITY, buy)
    if state is TradingState.ACTIVE:
        assert reason is None
    else:
        assert reason == "buy_not_permitted"
    # SELLs keep flowing under every entry latch, as on the decision path.
    assert runtime._advance_submit_block_reason(Market.EQUITY, sell) is None


# --------------------------------------------------------------------------- #
# Q2-3-2: every clause of the LIVE doctor refusal, one case each
# --------------------------------------------------------------------------- #

_LIVE_KEY = "AKlive123456"
_LIVE_SECRET = "AKlivesecret"


def _live_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mutate: Any = None,
    approval_key: str = "k" * 32,
    live_key: str = _LIVE_KEY,
    live_secret: str = _LIVE_SECRET,
    email_problems: list[str] | None = None,
) -> str | None:
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", approval_key)
    monkeypatch.setattr(manager, "get_paper_credentials", lambda: ("PKpaperkey12", "PKpapersecret"))
    monkeypatch.setattr(
        email_alerts_module, "validate_email_setup", lambda settings, env=None: list(email_problems or [])
    )
    cfg = _live_cfg(tmp_path)
    if mutate is not None:
        mutate(cfg)
    return manager._live_doctor_refusal(cfg, live_key, live_secret)


def _set_cap(value: float) -> Any:
    def mutate(cfg: Any) -> None:
        cfg.live.hard_dollar_cap = value

    return mutate


def _set_approval_path(value: str) -> Any:
    def mutate(cfg: Any) -> None:
        cfg.live.operator_approval_path = value

    return mutate


def _set_decision_db(value: str) -> Any:
    def mutate(cfg: Any) -> None:
        cfg.runtime.decision_db_path = value

    return mutate


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        pytest.param({"mutate": _set_cap(-1.0)}, "hard_dollar_cap", id="negative-cap"),
        pytest.param({"mutate": _set_cap(float("nan"))}, "hard_dollar_cap", id="nan-cap"),
        pytest.param({"mutate": _set_approval_path("")}, "operator_approval_path is not set", id="empty-approval-path"),
        pytest.param({"approval_key": "k" * 31}, "PARAYABUCK_OPERATOR_APPROVAL_KEY", id="31-char-approval-key"),
        pytest.param({"live_secret": "PKpapersecret"}, "share a key", id="secret-collision"),
        pytest.param(
            {"mutate": _set_decision_db(DEFAULT_DECISION_DB_PATH)},
            "PAPER decision database",
            id="paper-decision-db",
        ),
        pytest.param({"email_problems": ["smtp host is not set"]}, "smtp host is not set", id="email-problems"),
    ],
)
def test_each_live_doctor_clause_refuses_its_own_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any], expected: str
) -> None:
    refusal = _live_refusal(tmp_path, monkeypatch, **kwargs)

    assert refusal is not None
    assert refusal.startswith("LIVE start refused")
    assert expected in refusal


def test_live_doctor_accepts_the_exact_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A 32-character approval key and a positive cap sit on the accepted side.
    assert _live_refusal(tmp_path, monkeypatch, mutate=_set_cap(1e-9)) is None


def test_live_doctor_refuses_a_group_readable_credential_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("PARAYABUCK_NOTHING=1\n", encoding="utf-8")
    os.chmod(env_path, 0o644)

    refusal = _live_refusal(tmp_path, monkeypatch)

    assert refusal is not None and "chmod 600" in refusal

    os.chmod(env_path, 0o600)
    assert _live_refusal(tmp_path, monkeypatch) is None


# --------------------------------------------------------------------------- #
# Q2-3-3: a reset overtaken by a concurrent reset during the account read is a no-op
# --------------------------------------------------------------------------- #


class _RacingBroker(_ServiceBroker):
    """Its first account read is overtaken by a concurrent operator reset."""

    def __init__(self, service: Any, equity: float) -> None:
        super().__init__(service, equity)
        self.raced = False

    def account(self) -> Any:
        if not self.raced:
            self.raced = True
            assert self._service.reset_peak_equity(95_000.0, "acct", expected_epoch=0)
        return super().account()


class _HighWaterRepository(_Repository):
    """The reset's repository seam: the persisted high-water sits at the active baseline."""

    def high_water(self, account_id: str, *, epoch: int | None = None) -> float:
        return 0.0


def test_reset_overtaken_during_the_account_read_is_a_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, base_broker, sidecar, ledger = _epoch_setup(tmp_path, repository=_HighWaterRepository())
    runtime.broker = _RacingBroker(base_broker._service, equity=94_000.0)  # type: ignore[assignment]
    emitted: list[str] = []
    monkeypatch.setattr(runtime_module.alerts, "emit", lambda kind, **_fields: emitted.append(kind))

    baseline = runtime.start_new_risk_epoch()

    # The concurrent reset's baseline stands; this reset does not re-baseline.
    assert baseline == pytest.approx(95_000.0)
    assert runtime._last_risk_epoch_reset["changed"] is False
    assert runtime._last_risk_epoch_reset["epoch"] == 1
    assert _sidecar(sidecar)["epoch"] == 1
    assert "risk_epoch_reset" not in emitted
    ledger.close()


# --------------------------------------------------------------------------- #
# Q2-3-4: backup set members that are invalid, unreadable or do not read back
# --------------------------------------------------------------------------- #


def _db(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE decisions (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        connection.execute("INSERT INTO decisions (payload) VALUES ('synthetic')")
    return path


def _runtime_dir(tmp_path: Path) -> tuple[Path, Path]:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    return runtime_dir, _db(runtime_dir / "parayabuck.sqlite3")


def _capture_alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    alerts: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(alerts_module, "emit", lambda kind, **fields: alerts.append((kind, fields)))
    return alerts


def _member(destination: Path, name: str) -> Path:
    return destination.with_name(f"{destination.stem}.{name}")


def test_invalid_second_sidecar_keeps_the_valid_first_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir, source = _runtime_dir(tmp_path)
    (runtime_dir / "paper_equity_peak.json").write_text(_PEAK, encoding="utf-8")
    (runtime_dir / "live_equity_peak.json").write_text('{"peak_equity": -5}', encoding="utf-8")
    alerts = _capture_alerts(monkeypatch)
    backup_dir = tmp_path / "backups"

    destination = backup.backup_database(source, backup_dir, retention_days=7)

    assert _member(destination, "paper_equity_peak.json").read_text(encoding="utf-8") == _PEAK
    assert not _member(destination, "live_equity_peak.json").exists()
    assert backup.verify_backup_set(destination)
    assert [fields["sidecar_path"] for _, fields in alerts] == [str(runtime_dir / "live_equity_peak.json")]
    assert list(backup_dir.glob(f"{backup.STAGE_PREFIX}*")) == []


def test_unreadable_sidecar_is_left_out_and_reported_as_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir, source = _runtime_dir(tmp_path)
    (runtime_dir / "paper_equity_peak.json").write_bytes(b"\xff\xfe\x00 not utf-8")
    alerts = _capture_alerts(monkeypatch)
    backup_dir = tmp_path / "backups"

    destination = backup.backup_database(source, backup_dir, retention_days=7)

    assert sorted(path.name for path in backup_dir.iterdir()) == [destination.name]
    assert [kind for kind, _ in alerts] == ["backup_failed"]
    assert "unreadable" in alerts[0][1]["message"]


def test_sidecar_copy_that_does_not_read_back_is_left_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_dir, source = _runtime_dir(tmp_path)
    (runtime_dir / "paper_equity_peak.json").write_text(_PEAK, encoding="utf-8")
    (runtime_dir / "live_equity_peak.json").write_text(_PEAK, encoding="utf-8")
    real_stage = backup._stage_sidecar

    def failing_for_live(target_dir: Path, name: str, payload: str) -> Path | None:
        return None if name == "live_equity_peak.json" else real_stage(target_dir, name, payload)

    monkeypatch.setattr(backup, "_stage_sidecar", failing_for_live)
    alerts = _capture_alerts(monkeypatch)
    backup_dir = tmp_path / "backups"

    destination = backup.backup_database(source, backup_dir, retention_days=7)

    assert _member(destination, "paper_equity_peak.json").exists()
    assert not _member(destination, "live_equity_peak.json").exists()
    assert [fields["reason"] for _, fields in alerts] == ["sidecar_copy_failed"]
    assert list(backup_dir.glob(f"{backup.STAGE_PREFIX}*")) == []


def test_halt_marker_copy_that_does_not_read_back_is_left_out_and_alerted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir, source = _runtime_dir(tmp_path)
    marker = operator_halt_marker_path(runtime_dir)
    write_operator_halt(marker, reason="synthetic halt", user="test")
    real_stage = backup._stage_sidecar

    def failing_for_halt(target_dir: Path, name: str, payload: str) -> Path | None:
        return None if name == backup.HALT_COPY_NAME else real_stage(target_dir, name, payload)

    monkeypatch.setattr(backup, "_stage_sidecar", failing_for_halt)
    alerts = _capture_alerts(monkeypatch)
    backup_dir = tmp_path / "backups"

    destination = backup.backup_database(source, backup_dir, retention_days=7)

    assert not _member(destination, backup.HALT_COPY_NAME).exists()
    assert backup.verify_backup_set(destination)
    assert [(fields["reason"], fields["sidecar_path"]) for _, fields in alerts] == [
        ("sidecar_copy_failed", str(marker))
    ]


def test_stage_sidecar_refuses_a_staged_copy_that_reads_back_different(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_read_text = Path.read_text

    def corrupt_on_read_back(self: Path, *args: Any, **kwargs: Any) -> str:
        text = original_read_text(self, *args, **kwargs)
        return text[:-1] if self.name.startswith(backup.STAGE_PREFIX) else text

    monkeypatch.setattr(Path, "read_text", corrupt_on_read_back)

    assert backup._stage_sidecar(tmp_path, "paper_equity_peak.json", _PEAK) is None
    assert list(tmp_path.glob(f"{backup.STAGE_PREFIX}*")) == []


# --------------------------------------------------------------------------- #
# Q2-3-5: config bounds at their exact boundaries
# --------------------------------------------------------------------------- #


def _shipped_raw() -> dict[str, Any]:
    return yaml.safe_load(_SHIPPED.read_text(encoding="utf-8"))


def _load_mutated(tmp_path: Path, mutate: Any) -> Any:
    raw = _shipped_raw()
    mutate(raw)
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(str(path))


def _set(section: str, key: str, value: object) -> Any:
    def mutate(raw: dict[str, Any]) -> None:
        raw.setdefault(section, {})[key] = value

    return mutate


def _set_param(key: str, value: object) -> Any:
    def mutate(raw: dict[str, Any]) -> None:
        raw["strategy"]["equity"]["parameters"][key] = value

    return mutate


def _set_gain_bands(bands: list[float]) -> Any:
    def mutate(raw: dict[str, Any]) -> None:
        raw["execution"]["downsize_gain_band"]["bands"] = bands

    return mutate


def _plain_sma_rule(raw: dict[str, Any]) -> None:
    parameters = raw["strategy"]["equity"]["parameters"]
    for key in ("tsm_bars", "donchian_bars", "early_reentry_weight"):
        parameters.pop(key, None)
    parameters["trend_rule"] = "sma"


@pytest.mark.parametrize("rule", [None, "sma"], ids=["shipped-rule", "plain-sma-rule"])
def test_sma_bars_of_one_is_rejected_and_two_is_the_floor(tmp_path: Path, rule: str | None) -> None:
    def mutate(value: int) -> Any:
        def apply(raw: dict[str, Any]) -> None:
            if rule == "sma":
                _plain_sma_rule(raw)
            raw["strategy"]["equity"]["parameters"]["sma_bars"] = value

        return apply

    with pytest.raises(ValueError, match="sma_bars"):
        _load_mutated(tmp_path, mutate(1))
    assert _load_mutated(tmp_path, mutate(2)).strategy.equity.parameters["sma_bars"] == 2


def test_boolean_max_positions_is_rejected_at_load(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_positions must be an integer"):
        _load_mutated(tmp_path, _set("risk", "max_positions", True))


def test_vol_target_accepts_the_ceiling_and_rejects_just_above(tmp_path: Path) -> None:
    assert _load_mutated(tmp_path, _set_param("vol_target", 0.5)).strategy.equity.parameters["vol_target"] == 0.5
    with pytest.raises(ValueError, match="vol_target must be at most"):
        _load_mutated(tmp_path, _set_param("vol_target", 0.51))


def test_gain_band_ceiling_is_inclusive_at_twenty_percent(tmp_path: Path) -> None:
    _load_mutated(tmp_path, _set_gain_bands([0.005, 0.03, 0.2]))
    with pytest.raises(ValueError, match="bands must be in"):
        _load_mutated(tmp_path, _set_gain_bands([0.005, 0.03, 0.21]))


# --------------------------------------------------------------------------- #
# Q2-3-6: releasing the breaker's halt hands over to a non-reconciliation reason first
# --------------------------------------------------------------------------- #


def _halted_runtime(reasons: list[str], breaker: str | None) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime._halt_reasons = list(reasons)
    runtime._circuit_breaker_reason = breaker
    return runtime


def test_releasing_the_breaker_hands_over_to_a_non_reconciliation_reason() -> None:
    runtime = _halted_runtime(
        ["reconciliation_failed", "portfolio_drawdown_halt", "ownership_drift"], "ownership_drift"
    )

    runtime._release_halt_reason("ownership_drift")

    assert runtime._halt_reasons == ["reconciliation_failed", "portfolio_drawdown_halt"]
    assert runtime._circuit_breaker_reason == "portfolio_drawdown_halt"


def test_releasing_the_last_other_halt_leaves_the_reconciliation_reason_as_breaker() -> None:
    runtime = _halted_runtime(["portfolio_drawdown_halt", "reconciliation_failed"], "portfolio_drawdown_halt")

    runtime._release_halt_reason("portfolio_drawdown_halt")

    assert runtime._circuit_breaker_reason == "reconciliation_failed"


def test_releasing_a_halt_that_is_not_the_breaker_keeps_the_breaker() -> None:
    runtime = _halted_runtime(["ownership_drift", "reconciliation_failed"], "reconciliation_failed")

    runtime._release_halt_reason("ownership_drift")

    assert runtime._circuit_breaker_reason == "reconciliation_failed"


def test_releasing_the_only_halt_clears_the_breaker() -> None:
    runtime = _halted_runtime(["ownership_drift"], "ownership_drift")

    runtime._release_halt_reason("ownership_drift")

    assert runtime._halt_reasons == []
    assert runtime._circuit_breaker_reason is None


# --------------------------------------------------------------------------- #
# Q2-3-7: final status of a 'calculated' broker order (done for the day)
# --------------------------------------------------------------------------- #


def _calculated_status(ordered: Any, filled: float) -> str:
    service = ReconciliationService.__new__(ReconciliationService)
    service._broker = SimpleNamespace(_field=lambda value, name, default=None: getattr(value, name, default))
    return service._calculated_status(SimpleNamespace(qty=ordered), filled)


@pytest.mark.parametrize(
    ("ordered", "filled", "expected"),
    [
        pytest.param(100.0, 100.0, "filled", id="fully-filled"),
        pytest.param(100.0, 100.0 - 1e-7, "filled", id="within-the-relative-tolerance"),
        pytest.param(100.0, 99.5, "done_for_day", id="half-a-share-short"),
        pytest.param(100.0, 100.0 - 1e-5, "done_for_day", id="beyond-the-relative-tolerance"),
        pytest.param(None, 3.0, "filled", id="no-quantity-with-fills"),
        pytest.param(None, 0.0, "done_for_day", id="no-quantity-no-fills"),
        pytest.param("n/a", 0.0, "done_for_day", id="unreadable-quantity-no-fills"),
        pytest.param(0.0, 0.0, "done_for_day", id="zero-quantity-no-fills"),
    ],
)
def test_calculated_order_is_filled_only_when_fully_filled(ordered: Any, filled: float, expected: str) -> None:
    assert _calculated_status(ordered, filled) == expected


# --------------------------------------------------------------------------- #
# Q2-3-8: the flat-broker reconcile scope closes on exit and on error
# --------------------------------------------------------------------------- #


def test_flat_reconcile_scope_is_closed_on_exit_and_on_error() -> None:
    assert _FLAT_BROKER_ALLOWED.get() is False
    with flat_broker_reconcile_scope():
        assert _FLAT_BROKER_ALLOWED.get() is True
        with flat_broker_reconcile_scope():
            assert _FLAT_BROKER_ALLOWED.get() is True
        assert _FLAT_BROKER_ALLOWED.get() is True
    assert _FLAT_BROKER_ALLOWED.get() is False

    with pytest.raises(RuntimeError, match="reconcile failed"):
        with flat_broker_reconcile_scope():
            raise RuntimeError("reconcile failed")
    assert _FLAT_BROKER_ALLOWED.get() is False


# --------------------------------------------------------------------------- #
# Q2-3-9: the absolute share-count floor of the quantity match
# --------------------------------------------------------------------------- #


def test_quantity_match_floor_is_one_hundred_millionth_of_a_share() -> None:
    assert QUANTITY_MATCH_FLOOR == pytest.approx(1e-8)
    assert quantities_agree(0.0, QUANTITY_MATCH_FLOOR) is True
    assert quantities_agree(0.0, 1.5e-8) is False
    assert quantities_agree(1.0, 1.0 + 5e-9) is True


# --------------------------------------------------------------------------- #
# Q2-3-10: a managed quantity above the booked one by less than a billionth is not ahead
# --------------------------------------------------------------------------- #


def _ahead_runtime(booked: float) -> PortfolioRuntime:
    runtime = PortfolioRuntime.__new__(PortfolioRuntime)
    runtime.account_id = "acct"
    runtime.market = Market.EQUITY
    runtime.repository = SimpleNamespace(owned_quantities=lambda account_id, market: {"QLD": booked})
    return runtime


@pytest.mark.parametrize(
    ("managed", "ahead"),
    [
        pytest.param(1.0 + 5e-10, False, id="below-the-tolerance"),
        pytest.param(1.0 + 2e-9, True, id="above-the-tolerance"),
        pytest.param(0.5, False, id="behind-the-book"),
    ],
)
def test_snapshot_ahead_of_owned_uses_a_one_billionth_tolerance(managed: float, ahead: bool) -> None:
    runtime = _ahead_runtime(booked=1.0)
    account = SimpleNamespace(
        positions=(SimpleNamespace(symbol="QLD", market=Market.EQUITY, managed_qty=managed),),
    )

    reason = runtime._snapshot_ahead_of_owned(account, (SimpleNamespace(symbol="QLD"),))  # type: ignore[arg-type]

    assert (reason is not None) is ahead
    if ahead:
        assert "symbol=QLD" in reason


# --------------------------------------------------------------------------- #
# Q2-3-11: residual SELL statuses (the 'cancelled' spelling) and the fully filled guard
# --------------------------------------------------------------------------- #


def _residual_result(tmp_path: Path, repo: PortfolioStateRepository, decision_id: str) -> Any:
    runtime = _runtime(tmp_path, repo)
    result = runtime._residual_sell_result(decision_id, tuple(repo.orders_for_decision(decision_id, side="sell")))
    return result, runtime


def test_cancelled_spelling_leaves_the_unfilled_remainder_as_a_residual(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    decision_id = _decision(repo, T0)
    _record_sell(repo, decision_id, "pbp-cx", 10.0, "cancelled", T0)

    result, runtime = _residual_result(tmp_path, repo, decision_id)

    assert result is not None and result.status == "residual_sell_submitted"
    sent = runtime.broker.submitted  # type: ignore[attr-defined]
    assert [order.client_order_id for order in sent] == ["pbp-cx-residual1"]
    assert sent[0].quantity == pytest.approx(10.0)


def test_canceled_sell_that_filled_in_full_leaves_no_residual(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    decision_id = _decision(repo, T0)
    _record_sell(repo, decision_id, "pbp-done", 10.0, "canceled", T0)
    _fill(repo, "pbp-done", 10.0, 100.0, "canceled")
    # The tail is in the residual status set, so only the fully filled guard can clear it.
    assert repo.orders_for_decision(decision_id, side="sell")[0]["status"] == "canceled"

    result, runtime = _residual_result(tmp_path, repo, decision_id)

    assert result is None
    assert runtime.broker.submitted == []  # type: ignore[attr-defined]


def test_canceled_sell_with_an_unfilled_remainder_is_resent_for_the_remainder(tmp_path: Path) -> None:
    repo = PortfolioStateRepository(tmp_path / "runtime.sqlite3")
    decision_id = _decision(repo, T0)
    _record_sell(repo, decision_id, "pbp-part", 10.0, "canceled", T0)
    _fill(repo, "pbp-part", 4.0, 100.0, "canceled")

    result, runtime = _residual_result(tmp_path, repo, decision_id)

    assert result is not None
    sent = runtime.broker.submitted  # type: ignore[attr-defined]
    assert [order.quantity for order in sent] == [pytest.approx(6.0)]


# --------------------------------------------------------------------------- #
# Q2-1-1: a hard halt's re-queued close batch waits for the reset's reconcile
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("reason", ["ownership_drift", "portfolio_runtime_failure", "decision_worker_crashed"])
def test_hard_halt_reset_reconciles_while_the_halt_still_gates_the_requeued_batch(tmp_path: Path, reason: str) -> None:
    """The re-queued session-close batch is decided only after the breaker clears, and the
    breaker clears only after the reset's reconcile: the reconcile sees the halt still in force."""
    from tests.test_audit2_fix_D1 import CLEAN
    from tests.test_audit2_fix_D1 import _runtime as _reset_runtime

    runtime, ledger = _reset_runtime(tmp_path)
    seen: list[str | None] = []

    def reconcile() -> dict[str, list[str]]:
        seen.append(runtime._circuit_breaker_reason)
        return dict(CLEAN)

    runtime.broker.reconcile = reconcile  # type: ignore[attr-defined]
    runtime._raise_halt(reason)

    runtime.reset_safety_lock()

    assert seen == [reason]
    assert runtime._circuit_breaker_reason is None
    ledger.close()


# --------------------------------------------------------------------------- #
# Q2-1-2: the FX quote names the publication date, so a stale rate is visible
# --------------------------------------------------------------------------- #


def test_fx_quote_carries_the_publication_date_of_a_rate_older_than_the_previous_business_day() -> None:
    from datetime import date

    from parayabuck.tax.rates import RateBook, previous_weekday

    trade_date = date(2026, 10, 27)
    published = date(2026, 10, 22)
    quote = RateBook(usd_try={published: 35.0}, yiufe={}).usd_try_for_trade_date(trade_date)

    assert quote.rate == 35.0
    assert quote.published_on == published
    # The report note this would drive: the rate is older than the previous business day.
    assert quote.published_on < previous_weekday(trade_date)


# --------------------------------------------------------------------------- #
# Q2-1-5: the real LIVE doctor refusal stops a manual and an unattended LIVE start
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("auto_start", [True, False], ids=["unattended", "manual"])
def test_real_doctor_refusal_stops_a_live_start_before_any_runtime_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, auto_start: bool
) -> None:
    from tests.test_live_credentials_and_standing import _live_manager, _write_standing

    # The shipped test settings have e-mail alerts off: doctor's real gate refuses.
    manager = _live_manager(tmp_path, monkeypatch)
    _write_standing(load_config(str(manager.config_path)))
    monkeypatch.setattr(
        "parayabuck.runtime.EngineRuntime", lambda **_kwargs: pytest.fail("a refused LIVE start built a runtime")
    )

    with pytest.raises(ValueError, match="alerting.email.enabled is false"):
        manager.start_engine(auto_start=auto_start)

    assert manager.runtime is None
