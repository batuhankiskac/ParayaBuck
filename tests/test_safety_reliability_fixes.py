from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import parayabuck.runtime as runtime_module
from parayabuck.config import Config, RuntimeMode
from parayabuck.execution.alpaca_reconciliation import (
    RECONCILIATION_CATEGORIES,
    RECONCILIATION_DRIFT_CATEGORIES,
    ReconciliationReport,
    ReconciliationResultError,
)
from parayabuck.live_safety import LiveOperatorApproval, LiveStartupProof
from parayabuck.runtime import EngineRuntime
from parayabuck.universe import preflight
from parayabuck.web.managers.backtest import BacktestManager

_CLEAN_RECONCILIATION = {category: [] for category in RECONCILIATION_CATEGORIES}
_TEST_APPROVAL_SECRET = "t" * 32


class _BrokerSpy:
    account_id = "acct"

    def __init__(self) -> None:
        self.account_calls = 0
        self.reconcile_calls = 0
        self.submit_calls = 0
        self.cancel_calls = 0

    def account(self) -> SimpleNamespace:
        self.account_calls += 1
        return SimpleNamespace(account_id=self.account_id)

    def reconcile(self) -> dict[str, list[str]]:
        self.reconcile_calls += 1
        return dict(_CLEAN_RECONCILIATION)


def _proof(
    *,
    account_id: str = "acct",
    fingerprint: str | None = None,
    digests: tuple[str, ...] = ("digest",),
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> LiveStartupProof:
    issued = issued_at or datetime.now(UTC) - timedelta(seconds=1)
    return LiveStartupProof(
        account_id=account_id,
        credential_fingerprint=fingerprint or hashlib.sha256(b"test-api\0test-secret").hexdigest(),
        configuration_digests=digests,
        issued_at=issued,
        expires_at=expires_at or issued + timedelta(minutes=5),
    )


def _approval_file(path: Path, *, account_id: str = "acct", digests: tuple[str, ...] = ("digest",)) -> None:
    approval = LiveOperatorApproval(
        operator_id="test-operator",
        account_id=account_id,
        configuration_digests=digests,
        maximum_dollar_exposure=100.0,
        approved_at=datetime.now(UTC) - timedelta(seconds=1),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        nonce="test-nonce",
    ).signed(_TEST_APPROVAL_SECRET)
    path.write_text(json.dumps(approval.to_dict()), encoding="utf-8")


def _live_runtime(proof: LiveStartupProof | None, approval_path: Path, broker: _BrokerSpy) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.api_key = "test-api"
    runtime.secret_key = "test-secret"
    runtime.broker = broker
    runtime._live_startup_proof = proof
    runtime.app_config = SimpleNamespace(
        live=SimpleNamespace(
            operator_approval_key_env="PB_TEST_OPERATOR_KEY",
            operator_approval_path=str(approval_path),
        )
    )
    return runtime


def test_live_startup_proof_rejects_missing_or_stale_before_broker_calls(tmp_path: Path) -> None:
    stale = _proof(
        issued_at=datetime.now(UTC) - timedelta(minutes=2),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    for proof in (None, stale):
        broker = _BrokerSpy()
        runtime = _live_runtime(proof, tmp_path / "approval.json", broker)
        with pytest.raises(RuntimeError, match="missing or expired"):
            runtime._verify_live_startup_proof(("digest",))
        assert broker.account_calls == 0
        assert broker.reconcile_calls == 0
        assert broker.submit_calls == 0
        assert broker.cancel_calls == 0


def test_live_startup_proof_rejects_wrong_credential_or_config_before_broker_calls(tmp_path: Path) -> None:
    approval_path = tmp_path / "approval.json"
    _approval_file(approval_path)
    cases = (
        _proof(fingerprint="wrong-fingerprint"),
        _proof(digests=("changed",)),
    )
    for proof in cases:
        broker = _BrokerSpy()
        runtime = _live_runtime(proof, approval_path, broker)
        with pytest.raises(RuntimeError, match="startup proof"):
            runtime._verify_live_startup_proof(("digest",))
        assert broker.account_calls == 0
        assert broker.reconcile_calls == 0


def test_live_startup_proof_rejects_invalid_hmac_before_broker_calls(tmp_path: Path, monkeypatch) -> None:
    approval_path = tmp_path / "approval.json"
    approval = LiveOperatorApproval(
        operator_id="test-operator",
        account_id="acct",
        configuration_digests=("digest",),
        maximum_dollar_exposure=100.0,
        approved_at=datetime.now(UTC) - timedelta(seconds=1),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        nonce="test-nonce",
    ).signed("u" * 32)
    approval_path.write_text(json.dumps(approval.to_dict()), encoding="utf-8")
    monkeypatch.setenv("PB_TEST_OPERATOR_KEY", _TEST_APPROVAL_SECRET)
    broker = _BrokerSpy()
    runtime = _live_runtime(_proof(), approval_path, broker)
    with pytest.raises(RuntimeError, match="signature"):
        runtime._verify_live_startup_proof(("digest",))
    assert broker.account_calls == 0
    assert broker.reconcile_calls == 0


def test_live_startup_proof_rejects_wrong_account_before_mutation(tmp_path: Path, monkeypatch) -> None:
    approval_path = tmp_path / "approval.json"
    _approval_file(approval_path, account_id="other-acct")
    monkeypatch.setenv("PB_TEST_OPERATOR_KEY", _TEST_APPROVAL_SECRET)
    broker = _BrokerSpy()
    runtime = _live_runtime(_proof(account_id="other-acct"), approval_path, broker)
    with pytest.raises(RuntimeError, match="verified broker account"):
        runtime._verify_live_startup_proof(("digest",))
    # Account identity is read-only; no reconciliation or order boundary is
    # reached when the account proof is wrong.
    assert broker.account_calls == 1
    assert broker.reconcile_calls == 0
    assert broker.submit_calls == 0
    assert broker.cancel_calls == 0


def test_valid_live_proof_finishes_read_only_preflight_before_reconcile(tmp_path: Path, monkeypatch) -> None:
    approval_path = tmp_path / "approval.json"
    _approval_file(approval_path)
    monkeypatch.setenv("PB_TEST_OPERATOR_KEY", _TEST_APPROVAL_SECRET)
    broker = _BrokerSpy()
    runtime = _live_runtime(_proof(), approval_path, broker)

    runtime._verify_live_startup_proof(("digest",))
    broker.reconcile()

    assert broker.account_calls == 1
    assert broker.reconcile_calls == 1


def test_direct_live_start_without_proof_has_no_broker_boundary_calls(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_module.os,
        "getenv",
        lambda name, default=None: "YES" if name == "PARAYABUCK_ENABLE_LIVE" else default,
    )
    broker = _BrokerSpy()
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.mode = RuntimeMode.LIVE
    runtime.api_key = "test-api"
    runtime.secret_key = "test-secret"
    runtime.app_config = SimpleNamespace(
        live=SimpleNamespace(enabled=True, hard_dollar_cap=100.0),
    )
    runtime.broker = broker
    runtime._live_startup_proof = None
    runtime._expected_live_configuration_digests = lambda: ("digest",)
    runtime._running = False
    runtime._state = "stopped"
    runtime._stop_event = runtime_module.threading.Event()

    with pytest.raises(RuntimeError, match="missing or expired"):
        runtime.start()

    assert runtime._state == "stopped"
    assert broker.account_calls == 0
    assert broker.reconcile_calls == 0


def test_reconciliation_report_is_strict_and_extracts_every_drift_category() -> None:
    with pytest.raises(ReconciliationResultError):
        ReconciliationReport.from_result(None)
    with pytest.raises(ReconciliationResultError):
        ReconciliationReport.from_result({"MATCH": []})
    malformed = dict(_CLEAN_RECONCILIATION)
    malformed["MATCH"] = "clean"
    with pytest.raises(ReconciliationResultError):
        ReconciliationReport.from_result(malformed)

    for category in RECONCILIATION_DRIFT_CATEGORIES:
        result = dict(_CLEAN_RECONCILIATION)
        result[category] = ["item"]
        report = ReconciliationReport.from_result(result)
        assert report.has_drift
        assert report.drift_categories == {category: ("item",)}


class _CloseSpy:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def close(self) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("cleanup failure")


class _TradingSpy:
    instances: list[_TradingSpy] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self._session = _CloseSpy()
        self.instances.append(self)


class _HistorySpy(_CloseSpy):
    instances: list[_HistorySpy] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        super().__init__()
        self.instances.append(self)


def test_crypto_refresh_closes_owned_clients_on_success_and_failure(monkeypatch) -> None:
    """Only the crypto universe refresh owns clients; both the success and
    the failure leg must close them exactly once."""
    _TradingSpy.instances = []
    _HistorySpy.instances = []

    class CryptoTrading(_TradingSpy):
        def get_all_assets(self, _request):
            return []

    monkeypatch.setattr(preflight, "TradingClient", CryptoTrading)
    monkeypatch.setattr(preflight, "AlpacaHistoricalData", _HistorySpy)
    monkeypatch.setattr(preflight, "patch_session_timeout", lambda *_args, **_kwargs: None)

    def fake_refresh(_config, _assets, *, history_factory):
        history_factory()
        return SimpleNamespace(status="success")

    monkeypatch.setattr(preflight, "_refresh_crypto_universe_with_clients", fake_refresh)
    preflight.refresh_crypto_universe(Config(), "test-api", "test-secret")
    assert _TradingSpy.instances[0]._session.calls == 1
    assert _HistorySpy.instances[0].calls == 1

    def failing_refresh(_config, _assets, *, history_factory):
        history_factory()
        raise RuntimeError("primary refresh failure")

    monkeypatch.setattr(preflight, "_refresh_crypto_universe_with_clients", failing_refresh)
    with pytest.raises(RuntimeError, match="primary refresh failure"):
        preflight.refresh_crypto_universe(Config(), "test-api", "test-secret")
    assert _TradingSpy.instances[1]._session.calls == 1
    assert _HistorySpy.instances[1].calls == 1


def test_refresh_cleanup_failure_does_not_mask_primary_exception(monkeypatch) -> None:
    """A client whose close() fails must not replace the primary refresh
    failure the caller needs to see."""

    class BadHistory(_CloseSpy):
        def close(self) -> None:
            self.calls += 1
            raise RuntimeError("cleanup failure")

    class CryptoTrading(_TradingSpy):
        def get_all_assets(self, _request):
            return []

    trading = CryptoTrading()
    history = BadHistory()
    monkeypatch.setattr(preflight, "TradingClient", lambda *_args, **_kwargs: trading)
    monkeypatch.setattr(preflight, "patch_session_timeout", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(preflight, "AlpacaHistoricalData", lambda *_args, **_kwargs: history)

    def refresh_with_history(_config, _assets, *, history_factory):
        history_factory()
        raise RuntimeError("primary")

    monkeypatch.setattr(preflight, "_refresh_crypto_universe_with_clients", refresh_with_history)
    with pytest.raises(RuntimeError, match="^primary$"):
        preflight.refresh_crypto_universe(Config(), "test-api", "test-secret")
    assert history.calls == 1
    assert trading._session.calls == 1


def _stopped_backtest() -> BacktestManager:
    return BacktestManager(engine_state_provider=lambda: "stopped")


def test_backtest_invalid_parameter_terminalizes_before_launch() -> None:
    manager = _stopped_backtest()
    with pytest.raises(ValueError, match="Invalid parameter name"):
        manager.start({"parameters": {"bad-name": 1}})
    assert manager.state == "error"
    assert manager._thread is None
    assert manager.finished_at is not None


def test_backtest_thread_start_failure_terminalizes() -> None:
    manager = _stopped_backtest()

    class StartFails:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread start failed")

        def is_alive(self) -> bool:
            return False

    manager._thread_factory = StartFails
    with pytest.raises(RuntimeError, match="thread start failed"):
        manager.start({})
    assert manager.state == "error"
    assert manager.error == "thread start failed"
    assert manager._thread is None


@pytest.mark.parametrize("raw", ["[1, 2]", '"not an object"'])
def test_backtest_report_root_shape_failure_is_terminal(tmp_path: Path, raw: str) -> None:
    manager = _stopped_backtest()
    manager.output_dir = str(tmp_path)
    (tmp_path / "report.json").write_text(raw, encoding="utf-8")
    manager._finish(None)
    assert manager.state == "error"
    assert manager.error and "JSON nesnesi" in manager.error


def test_backtest_successful_report_is_done(tmp_path: Path) -> None:
    manager = _stopped_backtest()
    manager.output_dir = str(tmp_path)
    (tmp_path / "report.json").write_text(json.dumps({"summary": {"return": 1.0}}), encoding="utf-8")
    manager._finish(None)
    assert manager.state == "done"
    assert manager.error is None
    assert manager.summary == {"return": 1.0}
