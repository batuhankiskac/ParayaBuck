"""Test-gap regressions for the LIVE path (audit wave 2, Q2-7).

Offline: brokers and runtimes are fakes, credentials are synthetic, every
settings and approval file lives under ``tmp_path``, and the trading-host
identity is pinned. Nothing here touches data/, logs/, .env or the network.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import parayabuck.live_authorization as live_authorization
import parayabuck.runtime as runtime_module
from parayabuck.config import RuntimeMode, load_config
from parayabuck.core.models import Market
from parayabuck.execution.alpaca_reconciliation import RECONCILIATION_CATEGORIES
from parayabuck.live_authorization import create_standing_authorization, standing_authorization_file
from parayabuck.live_safety import LiveOperatorApproval, LiveStartupProof
from parayabuck.monitoring import alerts
from parayabuck.portfolio import product_strategy_from
from parayabuck.runtime import EngineRuntime
from parayabuck.runtime_universe import ActiveProductUniverse
from parayabuck.trading_host import trading_host_identity
from parayabuck.web.managers import engine as engine_module
from parayabuck.web.managers.engine import EngineManager, LiveArmToken
from tests.helpers import web_client

_REPO = Path(__file__).resolve().parents[1]
_SECRET = "0123456789abcdefghij" * 2  # operator-approval HMAC key and standing-authorization secret
_HOST = "host-t2"
_DIGEST = "digest"
_CLEAN_RECONCILIATION = {category: [] for category in RECONCILIATION_CATEGORIES}
_FINGERPRINT = hashlib.sha256(b"test-api\0test-secret").hexdigest()


@pytest.fixture(autouse=True)
def _pinned_host_and_alerts(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(live_authorization, "trading_host_identity", lambda: _HOST)
    monkeypatch.setattr("parayabuck.monitoring.journal.emit_journal", lambda event: None)
    alerts.reset_for_tests()
    alerts._throttle_seconds = 0.0
    yield
    alerts.reset_for_tests()


def _delivered(kind: str) -> int:
    delivered = alerts.alert_stats()["delivered"]
    assert isinstance(delivered, dict)
    return int(delivered.get(kind, 0))


def _write_settings(tmp_path: Path, *, cap: float = 1000.0) -> Path:
    raw = yaml.safe_load((_REPO / "config" / "settings.yaml").read_text(encoding="utf-8"))
    raw["runtime"] = {"mode": "live", "decision_db_path": str(tmp_path / "runtime" / "parayabuck.sqlite3")}
    raw["backtest"]["output_dir"] = str(tmp_path / "reports")
    raw["backup"]["path"] = str(tmp_path / "backups")
    raw["observability"] = {
        "journal_path": str(tmp_path / "observability.sqlite3"),
        "snapshot_path": str(tmp_path / "snapshot.json"),
    }
    raw["live"].update(
        {
            "enabled": True,
            "hard_dollar_cap": cap,
            "operator_approval_path": str(tmp_path / "live_approval.json"),
        }
    )
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def _write_standing(config: Any) -> None:
    authorization = create_standing_authorization(
        config,
        operator_id="owner",
        account_id="acct-live",
        days=14,
        secret=_SECRET,
        trading_host=_HOST,
    )
    path = standing_authorization_file(config)
    assert path is not None
    path.write_text(json.dumps(authorization.to_dict()), encoding="utf-8")


# --- Q2-7-1 / Q2-7-9: EngineRuntime.start() past the LIVE proof gate -------


class _LiveBroker:
    account_id = "acct"
    account_number = "912345678"  # a live account (paper accounts are "PA...")

    def __init__(self) -> None:
        self.account_calls = 0
        self.reconcile_calls = 0

    def account(self) -> SimpleNamespace:
        self.account_calls += 1
        return SimpleNamespace(account_id=self.account_id)

    def reconcile(self) -> dict[str, list[str]]:
        self.reconcile_calls += 1
        return dict(_CLEAN_RECONCILIATION)


class _RestoreReached(RuntimeError):
    pass


class _PortfolioSpy:
    """Stands in for PortfolioRuntime; records the cap it was built with."""

    instances: list[_PortfolioSpy] = []

    def __init__(self, *_args: Any, portfolio_digest: str, live_dollar_cap: float, **_kwargs: Any) -> None:
        self.configuration_digest = portfolio_digest
        self.constructed_cap = live_dollar_cap
        self.live_dollar_cap: float | None = None
        _PortfolioSpy.instances.append(self)


def _live_runtime_before_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cap: float = 1000.0,
    approval_cap: float = 100.0,
    portfolio_digest: str = _DIGEST,
) -> tuple[EngineRuntime, _LiveBroker, dict[str, bool]]:
    """A LIVE runtime whose ``start()`` runs through the proof gate, the
    portfolio composition and the cap clamp, and stops at the first step
    after them (``_restore_persistent_state``)."""
    monkeypatch.setenv("PARAYABUCK_TRADING_HOST", "1")
    monkeypatch.setenv("PARAYABUCK_ENABLE_LIVE", "YES")
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", _SECRET)
    monkeypatch.setattr(runtime_module, "config_fingerprint", lambda _config: "fp-test")
    config = load_config(str(_write_settings(tmp_path, cap=cap)))
    now = datetime.now(UTC)
    approval = LiveOperatorApproval(
        operator_id="owner",
        account_id="acct",
        configuration_digests=(_DIGEST,),
        maximum_dollar_exposure=approval_cap,
        approved_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=5),
        nonce="t2-nonce",
        config_fingerprint="fp-test",
        trading_host=trading_host_identity(),
    ).signed(_SECRET)
    Path(config.live.operator_approval_path).write_text(json.dumps(approval.to_dict()), encoding="utf-8")

    _PortfolioSpy.instances = []

    def _build_portfolio(*args: Any, **kwargs: Any) -> _PortfolioSpy:
        return _PortfolioSpy(*args, portfolio_digest=portfolio_digest, **kwargs)

    monkeypatch.setattr(runtime_module, "PortfolioRuntime", _build_portfolio)
    reached = {"restore": False}

    def _restore() -> None:
        reached["restore"] = True
        raise _RestoreReached("after composition")

    broker = _LiveBroker()
    strategy = product_strategy_from("equity_leveraged_trend_v1", config.strategy.equity.parameters)
    universe = ActiveProductUniverse.configured(Market.EQUITY, ("SPY", "QLD"))
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.mode = RuntimeMode.LIVE
    runtime.paper = False
    runtime.api_key = "test-api"
    runtime.secret_key = "test-secret"
    runtime.app_config = config
    runtime.broker = broker
    runtime._live_startup_proof = LiveStartupProof(
        account_id="acct",
        credential_fingerprint=_FINGERPRINT,
        configuration_digests=(_DIGEST,),
        issued_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(minutes=5),
    )
    runtime._running = False
    runtime._state = "stopped"
    runtime._stop_event = threading.Event()
    runtime._state_lock = threading.RLock()
    runtime._portfolio_lock = threading.RLock()
    runtime._symbols_by_market = {Market.EQUITY: {"SPY", "QLD"}}
    runtime._strategy_specs = ((strategy, ("SPY", "QLD"), 0.55),)
    runtime._product_universes_by_market = {Market.EQUITY: universe}
    runtime._portfolio_repository = object()
    runtime._portfolio_order_broker = lambda: None
    runtime._last_reconciliation_at = None
    runtime._circuit_breaker_reason = None
    runtime._pin_managed_symbols = lambda: None
    runtime._warn_foreign_inventory = lambda: None
    runtime._expected_live_configuration_digests = lambda: (_DIGEST,)
    runtime._restore_persistent_state = _restore
    runtime.ledger = SimpleNamespace(clear_safety_incident=lambda _account, _reason=None: None)
    return runtime, broker, reached


@pytest.mark.parametrize(
    ("cap", "approval_cap", "effective"),
    [
        (1000.0, 100.0, 100.0),  # the operator approval is the tighter bound
        (50.0, 100.0, 50.0),  # the configured hard cap is the tighter bound
    ],
)
def test_live_start_clamps_each_portfolio_to_the_lower_of_config_and_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cap: float, approval_cap: float, effective: float
) -> None:
    runtime, _broker, reached = _live_runtime_before_start(tmp_path, monkeypatch, cap=cap, approval_cap=approval_cap)

    with pytest.raises(_RestoreReached):
        runtime.start()

    assert reached["restore"] is True
    assert _PortfolioSpy.instances, "start() must compose the portfolio runtimes before the clamp"
    for spy in _PortfolioSpy.instances:
        assert spy.constructed_cap == pytest.approx(cap)
        assert spy.live_dollar_cap == pytest.approx(effective)


def test_live_start_refuses_when_the_portfolio_configuration_drifts_after_the_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, broker, reached = _live_runtime_before_start(tmp_path, monkeypatch, portfolio_digest="changed")

    with pytest.raises(RuntimeError, match="configuration changed during startup"):
        runtime.start()

    # Refused after the proof and the read-only account check, before any
    # persisted state is restored or any cap is applied.
    assert reached["restore"] is False
    assert all(spy.live_dollar_cap is None for spy in _PortfolioSpy.instances)
    assert broker.account_calls == 1


def test_live_start_refuses_when_live_is_not_enabled_before_any_broker_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, broker, _reached = _live_runtime_before_start(tmp_path, monkeypatch)
    # Config rejects runtime.mode=live with live.enabled=false; the runtime's
    # own gate is the second line, reached only past that invariant.
    object.__setattr__(runtime.app_config.live, "enabled", False)

    with pytest.raises(RuntimeError, match="requires live.enabled=true"):
        runtime.start()

    assert broker.account_calls == 0
    assert broker.reconcile_calls == 0


def test_live_start_refuses_a_zero_hard_cap_before_any_broker_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, broker, _reached = _live_runtime_before_start(tmp_path, monkeypatch)
    # LiveConfig's own invariant rejects an enabled block with a zero cap, so
    # the engine-level check is reached only by bypassing the frozen dataclass
    # on purpose: the runtime must refuse even when the config object is off.
    object.__setattr__(runtime.app_config.live, "hard_dollar_cap", 0.0)

    with pytest.raises(RuntimeError, match="positive live.hard_dollar_cap"):
        runtime.start()

    assert broker.account_calls == 0
    assert broker.reconcile_calls == 0


def test_live_start_refuses_without_the_enable_flag_before_any_broker_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, broker, _reached = _live_runtime_before_start(tmp_path, monkeypatch)
    monkeypatch.delenv("PARAYABUCK_ENABLE_LIVE")

    with pytest.raises(RuntimeError, match="PARAYABUCK_ENABLE_LIVE=YES"):
        runtime.start()

    assert broker.account_calls == 0
    assert broker.reconcile_calls == 0


# --- Q2-7-2 / Q2-7-4 / Q2-7-5: the web arming flow -------------------------


def _live_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EngineManager:
    path = _write_settings(tmp_path)
    monkeypatch.setenv("PARAYABUCK_TRADING_HOST", "1")
    monkeypatch.setenv("PARAYABUCK_ENABLE_LIVE", "YES")
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", _SECRET)
    monkeypatch.setattr(engine_module, "_scan_tracked_secrets", lambda root: None)
    manager = engine_module.EngineManager()
    manager.security_warning = None
    manager.config_path = path
    manager.env_path = tmp_path / "missing.env"
    monkeypatch.setattr(manager, "get_live_credentials", lambda: ("AKlive", "live-secret"))
    monkeypatch.setattr(manager, "get_paper_credentials", lambda: ("PKpaper", "paper-secret"))
    return manager


def _arm(
    manager: EngineManager, *, expires_in: timedelta = timedelta(minutes=2), config_digest: str | None = None
) -> None:
    now = datetime.now(UTC)
    key, secret = manager.get_live_credentials()
    manager._pending_live_arm = LiveArmToken(
        account_id="acct-live",
        account_suffix="1234",
        credential_fingerprint=manager._credential_fingerprint(key, secret),
        config_digest=config_digest or manager._config_digest(paper=False),
        issued_at=now,
        expires_at=now + expires_in,
    )


def _patch_live_broker(
    monkeypatch: pytest.MonkeyPatch, *, account_number: str = "912345678", suffix: str = "1234"
) -> list[Any]:
    created: list[Any] = []

    class _Broker:
        def __init__(self, _key: str, _secret: str, paper: bool = True) -> None:
            self.paper = paper
            self.account_id = "acct-live"
            self.account_number = account_number
            self.account_suffix = suffix
            self.closed = False
            created.append(self)

        def account(self) -> SimpleNamespace:
            return SimpleNamespace(account_id=self.account_id)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("parayabuck.execution.alpaca.AlpacaBroker", _Broker)
    return created


def test_request_live_arming_refuses_while_an_engine_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _live_manager(tmp_path, monkeypatch)
    created = _patch_live_broker(monkeypatch)
    monkeypatch.setattr(manager, "has_active_runtime", lambda: True)

    with pytest.raises(ValueError, match="motoru durdurun"):
        manager.request_live_arming()
    assert created == []


def test_request_live_arming_needs_the_enable_flag_and_live_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _live_manager(tmp_path, monkeypatch)
    created = _patch_live_broker(monkeypatch)

    monkeypatch.delenv("PARAYABUCK_ENABLE_LIVE")
    with pytest.raises(ValueError, match="PARAYABUCK_ENABLE_LIVE=YES"):
        manager.request_live_arming()

    monkeypatch.setenv("PARAYABUCK_ENABLE_LIVE", "YES")
    monkeypatch.setattr(manager, "get_live_credentials", lambda: ("", ""))
    with pytest.raises(ValueError, match="gerekli"):
        manager.request_live_arming()
    assert created == []
    assert manager._pending_live_arm is None


def test_request_live_arming_refuses_a_paper_account_and_closes_the_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _live_manager(tmp_path, monkeypatch)
    created = _patch_live_broker(monkeypatch, account_number="PA3PAPER")

    with pytest.raises(ValueError, match="does not resolve to a live"):
        manager.request_live_arming()

    assert manager._pending_live_arm is None
    assert len(created) == 1 and created[0].closed is True


def test_request_live_arming_needs_a_verified_account_suffix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _live_manager(tmp_path, monkeypatch)
    created = _patch_live_broker(monkeypatch, suffix="")

    with pytest.raises(ValueError, match="doğrulanamadı"):
        manager.request_live_arming()

    assert manager._pending_live_arm is None
    assert created[0].closed is True


def test_request_then_confirm_arms_the_live_path_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _live_manager(tmp_path, monkeypatch)
    created = _patch_live_broker(monkeypatch)

    assert manager.request_live_arming() == "1234"
    pending = manager._pending_live_arm
    assert pending is not None
    assert pending.account_id == "acct-live"
    assert pending.config_digest == manager._config_digest(paper=False)
    assert manager._live_armed is False
    assert created[0].closed is True

    manager.confirm_live_arming("LIVE 1234")
    assert manager._live_armed is True
    assert manager._pending_live_arm is None


def test_manual_live_start_with_a_fresh_arm_builds_the_proof_from_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _live_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(manager, "_live_doctor_refusal", lambda *_args: None)  # see the standing test module
    _arm(manager)
    manager.confirm_live_arming("LIVE 1234")

    captured: dict[str, Any] = {}

    class _FakeRuntime:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.broker = SimpleNamespace(close=lambda: None)

        def start(self) -> None:
            pass

        def stop(self) -> bool:
            return True

    monkeypatch.setattr("parayabuck.runtime.EngineRuntime", _FakeRuntime)
    manager.start_engine()

    proof = captured["live_startup_proof"]
    assert isinstance(proof, LiveStartupProof)
    assert proof.authorization == "operator_approval"
    assert proof.account_id == "acct-live"
    assert proof.credential_fingerprint == manager._credential_fingerprint("AKlive", "live-secret")
    assert proof.configuration_digests
    # The interactive arm is single-use: the start consumed it.
    assert manager._live_armed is False
    assert manager._live_arm_token is None
    manager.stop_engine()


def test_confirm_live_arming_refuses_an_expired_arm_and_invalidates_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _live_manager(tmp_path, monkeypatch)
    _arm(manager, expires_in=timedelta(seconds=-1))

    with pytest.raises(ValueError, match="eşleşmiyor"):
        manager.confirm_live_arming("LIVE 1234")

    assert manager._live_armed is False
    assert manager._pending_live_arm is None


def test_confirm_live_arming_refuses_when_the_settings_changed_after_the_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _live_manager(tmp_path, monkeypatch)
    _arm(manager, config_digest="digest-from-before-the-edit")

    with pytest.raises(ValueError, match="eşleşmiyor"):
        manager.confirm_live_arming("LIVE 1234")

    assert manager._live_armed is False
    assert manager._pending_live_arm is None


# --- Q2-7-3: /api/mode refuses LIVE without a valid arm -------------------


def test_api_mode_to_live_without_a_valid_arm_is_refused_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = EngineManager()
    manager.config_path = tmp_path / "settings.yaml"
    manager.config_path.write_text("runtime:\n  mode: paper\n", encoding="utf-8")
    manager.env_path = tmp_path / "missing.env"
    manager.security_warning = None
    manager.engine_state = "stopped"
    monkeypatch.setattr(manager, "get_live_credentials", lambda: ("AKlive", "live-secret"))
    monkeypatch.setattr(manager, "reset_broker", lambda: None)
    before = manager.config_path.read_text(encoding="utf-8")

    response = web_client(manager).post("/api/mode", json={"paper": False})

    assert response.status_code == 400
    assert "yeni bir canlı onay" in response.json()["detail"]
    assert manager.config_path.read_text(encoding="utf-8") == before


# --- Q2-7-8: the unattended LIVE start gate, one missing guard at a time --


@pytest.mark.parametrize("missing", ["enable_flag", "live_keys", "security_warning"])
def test_live_auto_start_refuses_and_alerts_when_one_live_guard_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    manager = _live_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(manager, "_live_doctor_refusal", lambda *_args: None)  # see the standing test module
    _write_standing(load_config(str(manager.config_path)))
    # Baseline: every guard passes, so the only variable below is the one removed.
    assert manager.should_auto_start() is True
    before = _delivered("live_rearm_required")

    if missing == "enable_flag":
        monkeypatch.delenv("PARAYABUCK_ENABLE_LIVE")
    elif missing == "live_keys":
        monkeypatch.setattr(manager, "get_live_credentials", lambda: ("", ""))
    else:
        manager.security_warning = "tracked secret found"

    assert manager.should_auto_start() is False
    assert _delivered("live_rearm_required") == before + 1


# --- Q2-7-7: endpoint and peak-sidecar selection for LIVE (paper=False) ---


def test_risk_peak_sidecar_name_follows_the_mode(tmp_path: Path) -> None:
    from parayabuck.execution.risk_peak import resolve_peak_state_path

    assert resolve_peak_state_path(False, None, tmp_path) == tmp_path / "live_equity_peak.json"
    assert resolve_peak_state_path(True, None, tmp_path) == tmp_path / "paper_equity_peak.json"
    explicit = tmp_path / "custom_peak.json"
    assert resolve_peak_state_path(False, explicit, tmp_path) == explicit
    # Without a state dir the default is the LIVE sidecar name (the test
    # session redirects the runtime directory itself; only the name is pinned).
    assert resolve_peak_state_path(False, None, None).name == "live_equity_peak.json"


def test_alpaca_client_selects_the_trading_endpoint_from_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    from parayabuck.execution import alpaca_client as client_module

    endpoints: list[bool] = []

    def _trading_client(_key: str, _secret: str, *, paper: bool) -> object:
        endpoints.append(paper)
        return object()

    monkeypatch.setattr(client_module, "TradingClient", _trading_client)
    client_module.AlpacaClient("key", "secret", paper=False)
    client_module.AlpacaClient("key", "secret")  # the default is PAPER

    assert endpoints == [False, True]


def test_broker_hands_its_paper_flag_to_the_client_and_the_peak_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    import parayabuck.execution.alpaca as alpaca_module

    seen: dict[str, bool] = {}

    class _Adapter:
        def __init__(self, _key: str, _secret: str, *, paper: bool, client: Any = None) -> None:
            seen["client"] = paper

        def close(self) -> None:
            pass

    class _Peak:
        def __init__(self, *, paper: bool, **_kwargs: Any) -> None:
            seen["peak"] = paper

    monkeypatch.setattr(alpaca_module, "AlpacaClient", _Adapter)
    monkeypatch.setattr(alpaca_module, "RiskPeakService", _Peak)

    alpaca_module.AlpacaBroker("key", "secret", paper=False)

    assert seen == {"client": False, "peak": False}


def test_trade_update_stream_connects_to_the_live_endpoint_when_not_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    import alpaca.trading.stream as sdk_stream

    from parayabuck.execution.alpaca_updates import AlpacaTradingUpdateStream

    constructed: list[dict[str, Any]] = []

    class _FakeStream:
        def __init__(self, _key: str, _secret: str, *, paper: bool, websocket_params: dict[str, Any]) -> None:
            constructed.append({"paper": paper, "websocket_params": websocket_params})
            self._cast = lambda frame: frame  # the SDK's frame cast, which the stream wraps

        def subscribe_trade_updates(self, _handler: Any) -> None:
            pass

    monkeypatch.setattr(sdk_stream, "TradingStream", _FakeStream)
    AlpacaTradingUpdateStream("key", "secret", paper=False, handler=lambda _update: None)
    AlpacaTradingUpdateStream("key", "secret", paper=True, handler=lambda _update: None)

    assert [item["paper"] for item in constructed] == [False, True]


# --- Q2-7-6: `tax import-activities --live` wiring (credentials, endpoint, ledger) ---


def _import_args(*, live: bool) -> Any:
    from argparse import Namespace

    return Namespace(config=None, live=live, since="2026-01-01")


def _patch_import_wiring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, credentials: tuple[str, str]) -> dict:
    import parayabuck.config as config_module
    import parayabuck.execution.alpaca_client as client_module
    import parayabuck.live_safety as live_safety
    import parayabuck.tax.cli as tax_cli

    database = tmp_path / "ledger.sqlite3"
    sqlite3.connect(str(database)).close()  # the import refuses to create a ledger, so one must exist
    seen: dict[str, Any] = {"clients": [], "credential_modes": []}

    class _Client:
        _client = SimpleNamespace(get=lambda *_a, **_k: None)

        def __init__(self, _key: str, _secret: str, *, paper: bool) -> None:
            seen["clients"].append(paper)

        def close(self) -> None:
            pass

    def _credentials(*, live: bool, env: Any = None) -> tuple[str, str]:
        seen["credential_modes"].append(live)
        return credentials

    monkeypatch.setattr(config_module, "load_env_file", lambda *a, **k: None)
    monkeypatch.setattr(live_safety, "mode_credentials", _credentials)
    monkeypatch.setattr(client_module, "AlpacaClient", _Client)
    monkeypatch.setattr(tax_cli, "_database_path", lambda *_a, **_k: database)
    monkeypatch.setattr(tax_cli, "fetch_account_id", lambda _get: "PA-or-live-1234")
    monkeypatch.setattr(tax_cli, "fetch_activities", lambda _get, _since: [])
    return seen


def test_tax_import_live_uses_the_live_credentials_and_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json as json_module

    import parayabuck.tax.cli as tax_cli

    seen = _patch_import_wiring(monkeypatch, tmp_path, credentials=("AKlive", "live-secret"))

    assert tax_cli._import_activities(_import_args(live=True)) == 0

    assert seen["credential_modes"] == [True]
    assert seen["clients"] == [False]
    report = json_module.loads(capsys.readouterr().out)
    assert report["live"] is True
    assert report["account_suffix"] == "1234"


def test_tax_import_default_uses_paper_credentials_and_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import parayabuck.tax.cli as tax_cli

    seen = _patch_import_wiring(monkeypatch, tmp_path, credentials=("PKpaper", "paper-secret"))

    assert tax_cli._import_activities(_import_args(live=False)) == 0

    assert seen["credential_modes"] == [False]
    assert seen["clients"] == [True]


def test_tax_import_live_refuses_without_live_credentials_before_any_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import parayabuck.tax.cli as tax_cli

    seen = _patch_import_wiring(monkeypatch, tmp_path, credentials=("", ""))

    assert tax_cli._import_activities(_import_args(live=True)) == 1

    assert seen["clients"] == []
    assert "not configured" in capsys.readouterr().err
