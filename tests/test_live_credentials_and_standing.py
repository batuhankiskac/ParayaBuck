"""B3 (separate LIVE credentials) and B2 (standing LIVE authorization).

Offline: brokers are fakes, credentials are synthetic, every path is under
``tmp_path`` and the trading-host identity is pinned by monkeypatch.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import parayabuck.live_authorization as live_authorization
from parayabuck import cli
from parayabuck.config import (
    DEFAULT_DECISION_DB_PATH,
    DEFAULT_LIVE_DECISION_DB_PATH,
    Config,
    RuntimeConfig,
    RuntimeMode,
    load_config,
)
from parayabuck.live_authorization import (
    STANDING_EXPIRED,
    STANDING_INVALID,
    STANDING_VALID,
    create_standing_authorization,
    live_configuration_digests,
    standing_authorization_file,
    standing_authorization_status,
)
from parayabuck.live_safety import (
    LiveApprovalError,
    LiveStartupProof,
    StandingLiveAuthorization,
    classify_account_environment,
    credential_env_names,
    verify_account_environment,
)
from parayabuck.monitoring import alerts
from parayabuck.runtime import EngineRuntime
from parayabuck.web.managers import engine as engine_module

_REPO = Path(__file__).resolve().parents[1]
_HMAC = "0123456789abcdef0123456789abcdef01234567"
_HOST = "host-a"


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


def _write_settings(tmp_path: Path, *, mode: str = "live", cap: float = 1000.0, **app: Any) -> Path:
    raw = yaml.safe_load((_REPO / "config" / "settings.yaml").read_text(encoding="utf-8"))
    raw["runtime"] = {"mode": mode, "decision_db_path": str(tmp_path / "runtime" / "parayabuck.sqlite3")}
    raw["backtest"]["output_dir"] = str(tmp_path / "reports")
    raw["backup"]["path"] = str(tmp_path / "backups")
    raw["observability"] = {
        "journal_path": str(tmp_path / "observability.sqlite3"),
        "snapshot_path": str(tmp_path / "snapshot.json"),
    }
    raw["app"].update(app)
    if mode == "live":
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


def _live_config(tmp_path: Path, **kwargs: Any) -> Config:
    return load_config(str(_write_settings(tmp_path, **kwargs)))


def _write_standing(config: Config, *, days: int = 14, account_id: str = "acct-live", **kwargs: Any) -> Path:
    authorization = create_standing_authorization(
        config,
        operator_id="owner",
        account_id=account_id,
        days=days,
        secret=_HMAC,
        trading_host=_HOST,
        **kwargs,
    )
    path = standing_authorization_file(config)
    assert path is not None
    path.write_text(json.dumps(authorization.to_dict()), encoding="utf-8")
    return path


# --- B3: separate credentials ---------------------------------------------


def test_each_mode_has_its_own_credential_names() -> None:
    assert credential_env_names(live=False) == ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")
    assert credential_env_names(live=True) == ("ALPACA_LIVE_API_KEY", "ALPACA_LIVE_SECRET_KEY")


def _env_manager(tmp_path: Path, mode: str, env_lines: list[str]) -> engine_module.EngineManager:
    manager = engine_module.EngineManager()
    manager.config_path = tmp_path / "settings.yaml"
    manager.config_path.write_text(yaml.safe_dump({"runtime": {"mode": mode}}), encoding="utf-8")
    manager.env_path = tmp_path / ".env"
    manager.env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    manager.env_path.chmod(0o600)
    return manager


def test_live_mode_never_falls_back_to_the_paper_key_names(tmp_path: Path) -> None:
    paper_only = ["ALPACA_API_KEY=PKpaper", "ALPACA_SECRET_KEY=paper-secret"]
    assert _env_manager(tmp_path, "paper", paper_only).get_credentials() == ("PKpaper", "paper-secret")
    assert _env_manager(tmp_path, "live", paper_only).get_credentials() == (None, None)
    both = [*paper_only, "ALPACA_LIVE_API_KEY=AKlive", "ALPACA_LIVE_SECRET_KEY=live-secret"]
    assert _env_manager(tmp_path, "live", both).get_credentials() == ("AKlive", "live-secret")
    # ...and PAPER never reads the LIVE names.
    paper = _env_manager(tmp_path, "paper", both)
    assert paper.get_credentials() == ("PKpaper", "paper-secret")
    assert paper.get_live_credentials() == ("AKlive", "live-secret")


@pytest.mark.parametrize(
    ("account_number", "key", "environment"),
    [
        ("PA3ABCDEF", "PKXYZ", "paper"),
        ("PA3ABCDEF", "", "paper"),
        ("", "PKXYZ", "paper"),
        ("912345678", "AKXYZ", "live"),
        ("912345678", "", "live"),
        ("PA3ABCDEF", "AKXYZ", "unknown"),
        ("912345678", "PKXYZ", "unknown"),
        ("", "", "unknown"),
    ],
)
def test_account_environment_classification(account_number: str, key: str, environment: str) -> None:
    assert classify_account_environment(account_number, key) == environment


def test_live_requires_a_live_account_and_paper_refuses_a_live_key() -> None:
    verify_account_environment(live=True, account_number="912345678", api_key="AKXYZ")
    for number, key in (("PA3ABCDEF", "AKXYZ"), ("", "AKXYZ"), ("912345678", "PKXYZ")):
        with pytest.raises(LiveApprovalError, match="LIVE start refused"):
            verify_account_environment(live=True, account_number=number, api_key=key)
    # PAPER is unchanged unless the evidence is unambiguously live.
    verify_account_environment(live=False, account_number="PA3ABCDEF", api_key="PKXYZ")
    verify_account_environment(live=False, account_number="fake-acct-1234", api_key="key")
    with pytest.raises(LiveApprovalError, match="PAPER start refused"):
        verify_account_environment(live=False, account_number="912345678", api_key="AKXYZ")


def test_live_uses_a_separate_default_ledger() -> None:
    assert RuntimeConfig(mode=RuntimeMode.LIVE).decision_db_path == DEFAULT_LIVE_DECISION_DB_PATH
    explicit = RuntimeConfig(mode=RuntimeMode.LIVE, decision_db_path="./data/runtime/parayabuck.sqlite3")
    assert explicit.decision_db_path == DEFAULT_LIVE_DECISION_DB_PATH
    assert RuntimeConfig(mode=RuntimeMode.LIVE, decision_db_path="x/live.db").decision_db_path == "x/live.db"
    assert RuntimeConfig(mode=RuntimeMode.PAPER).decision_db_path == DEFAULT_DECISION_DB_PATH


class _FakeLiveBroker:
    account_number = "912345678"
    keys: list[tuple[str, str, bool]] = []

    def __init__(self, key: str, secret: str, paper: bool) -> None:
        type(self).keys.append((key, secret, paper))
        self.account_id = "acct-live"
        self.account_suffix = "live"

    def account(self) -> object:
        return object()

    def close(self) -> None:
        pass


def _authorize_args(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "operator": "owner",
        "expires_minutes": 15,
        "standing_days": None,
        "revoke_standing": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_authorize_live_reads_only_the_live_key_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _live_config(tmp_path)
    _FakeLiveBroker.keys = []
    monkeypatch.setattr(cli, "AlpacaBroker", _FakeLiveBroker)
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", _HMAC)
    monkeypatch.setenv("ALPACA_API_KEY", "PKpaper")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "paper-secret")
    with pytest.raises(SystemExit, match="ALPACA_LIVE_API_KEY"):
        cli._authorize_live(config, _authorize_args())  # type: ignore[arg-type]
    assert _FakeLiveBroker.keys == []
    monkeypatch.setenv("ALPACA_LIVE_API_KEY", "AKlive")
    monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "live-secret")
    assert cli._authorize_live(config, _authorize_args()) == 0  # type: ignore[arg-type]
    assert _FakeLiveBroker.keys == [("AKlive", "live-secret", False)]
    assert json.loads((tmp_path / "live_approval.json").read_text())["account_id"] == "acct-live"


def test_authorize_live_refuses_a_key_that_resolves_to_a_paper_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _live_config(tmp_path)

    class _PaperAccount(_FakeLiveBroker):
        account_number = "PA3PAPER"

    monkeypatch.setattr(cli, "AlpacaBroker", _PaperAccount)
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", _HMAC)
    monkeypatch.setenv("ALPACA_LIVE_API_KEY", "AKlive")
    monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "live-secret")
    with pytest.raises(SystemExit, match="does not resolve to a live Alpaca account"):
        cli._authorize_live(config, _authorize_args())  # type: ignore[arg-type]
    assert not (tmp_path / "live_approval.json").exists()


# --- B2: standing LIVE authorization ---------------------------------------


def test_standing_authorization_roundtrip_and_thirty_day_ceiling(tmp_path: Path, monkeypatch) -> None:
    config = _live_config(tmp_path)
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", _HMAC)
    _write_standing(config, days=30)
    status = standing_authorization_status(config)
    assert status.status == STANDING_VALID and status.authorization is not None
    assert status.authorization.maximum_dollar_exposure == 1000.0
    with pytest.raises(ValueError, match="between 1 and 30"):
        create_standing_authorization(config, operator_id="o", account_id="a", days=31, secret=_HMAC)
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="at most 30 days"):
        StandingLiveAuthorization(
            operator_id="o",
            account_id="a",
            configuration_digests=("d",),
            config_fingerprint="f",
            maximum_dollar_exposure=1.0,
            trading_host="h",
            approved_at=now,
            expires_at=now + timedelta(days=30, seconds=1),
            nonce="n",
        )


def test_any_settings_cap_or_host_change_voids_the_standing_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", _HMAC)
    config = _live_config(tmp_path)
    _write_standing(config)
    assert standing_authorization_status(config).valid
    # An unrelated settings edit (app log level) no longer voids it: only the
    # trading-relevant sections are fingerprinted (S8-7) ...
    assert standing_authorization_status(_live_config(tmp_path, log_level="DEBUG")).status == STANDING_VALID
    # ... but the dollar cap still does ...
    assert standing_authorization_status(_live_config(tmp_path, cap=5000.0)).status == STANDING_INVALID
    # ... and another trading host.
    monkeypatch.setattr(live_authorization, "trading_host_identity", lambda: "host-b")
    invalid = standing_authorization_status(config)
    assert invalid.status == STANDING_INVALID and "trading host" in invalid.detail


def test_tampered_or_foreign_key_signed_authorization_is_invalid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", _HMAC)
    config = _live_config(tmp_path)
    path = _write_standing(config)
    raw = json.loads(path.read_text())
    raw["maximum_dollar_exposure"] = 99999.0
    path.write_text(json.dumps(raw))
    assert "signature" in standing_authorization_status(config).detail
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", "x" * 40)
    _write_standing(config)
    assert standing_authorization_status(config).status == STANDING_INVALID


def test_expiry_warning_and_expired_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", _HMAC)
    config = _live_config(tmp_path)
    _write_standing(config, days=10)
    now = datetime.now(UTC)
    assert not standing_authorization_status(config, now=now).expiring(now)
    late = now + timedelta(days=8)
    assert standing_authorization_status(config, now=late).expiring(late)
    assert standing_authorization_status(config, now=now + timedelta(days=11)).status == STANDING_EXPIRED


def test_runtime_sends_expiring_and_expired_notices_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", _HMAC)
    config = _live_config(tmp_path)
    _write_standing(config, days=10)
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.mode = RuntimeMode.LIVE
    runtime.app_config = config
    now = datetime.now(UTC)
    runtime._maybe_check_standing_authorization(now=now + timedelta(days=1))
    assert _delivered("live_authorization_expiring") == 0
    for _ in range(3):
        runtime._maybe_check_standing_authorization(now=now + timedelta(days=8))
    assert _delivered("live_authorization_expiring") == 1
    runtime._maybe_check_standing_authorization(now=now + timedelta(days=9, hours=1))
    assert _delivered("live_authorization_expiring") == 2  # one notice per remaining day
    for _ in range(2):
        runtime._maybe_check_standing_authorization(now=now + timedelta(days=11))
    assert _delivered("live_authorization_expired") == 1


class _LiveBrokerSpy:
    account_id = "acct-live"
    account_number = "912345678"

    def __init__(self) -> None:
        self.account_calls = 0

    def account(self) -> object:
        self.account_calls += 1
        return object()


def _standing_runtime(config: Config, broker: Any, proof: LiveStartupProof) -> EngineRuntime:
    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.api_key = "AKlive"
    runtime.secret_key = "live-secret"
    runtime.broker = broker
    runtime.app_config = config
    runtime._live_startup_proof = proof
    return runtime


def _standing_proof(config: Config, account_id: str = "acct-live") -> LiveStartupProof:
    now = datetime.now(UTC) - timedelta(seconds=1)
    return LiveStartupProof(
        account_id=account_id,
        credential_fingerprint=hashlib.sha256(b"AKlive\0live-secret").hexdigest(),
        configuration_digests=live_configuration_digests(config),
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        authorization="standing",
    )


def test_runtime_reverifies_the_standing_authorization_and_the_live_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", _HMAC)
    config = _live_config(tmp_path)
    _write_standing(config)
    digests = live_configuration_digests(config)
    approval = _standing_runtime(config, _LiveBrokerSpy(), _standing_proof(config))._verify_live_startup_proof(digests)
    assert isinstance(approval, StandingLiveAuthorization)
    # A proof naming another account never passes the standing binding.
    broker = _LiveBrokerSpy()
    with pytest.raises(LiveApprovalError, match="account does not match"):
        _standing_runtime(config, broker, _standing_proof(config, "acct-other"))._verify_live_startup_proof(digests)
    assert broker.account_calls == 0
    # B3: a key that resolves to a paper account is refused.
    paper_broker = _LiveBrokerSpy()
    paper_broker.account_number = "PA3PAPER"
    with pytest.raises(LiveApprovalError, match="does not resolve to a live"):
        _standing_runtime(config, paper_broker, _standing_proof(config))._verify_live_startup_proof(digests)


def _live_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **settings: Any) -> engine_module.EngineManager:
    path = _write_settings(tmp_path, **settings)
    monkeypatch.setenv("PARAYABUCK_TRADING_HOST", "1")
    monkeypatch.setenv("PARAYABUCK_ENABLE_LIVE", "YES")
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", _HMAC)
    monkeypatch.setattr(engine_module, "_scan_tracked_secrets", lambda root: None)
    manager = engine_module.EngineManager()
    manager.security_warning = None
    manager.config_path = path
    manager.env_path = tmp_path / "missing.env"
    monkeypatch.setattr(manager, "get_live_credentials", lambda: ("AKlive", "live-secret"))
    monkeypatch.setattr(manager, "get_paper_credentials", lambda: ("PKpaper", "paper-secret"))
    return manager


def test_live_without_standing_authorization_waits_for_rearm_and_emails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _live_manager(tmp_path, monkeypatch)
    assert manager.should_auto_start() is False
    assert _delivered("live_rearm_required") == 1


def test_valid_standing_authorization_allows_the_unattended_live_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _live_manager(tmp_path, monkeypatch)
    # The shipped test config has e-mail alerts off, which doctor's LIVE gate
    # refuses; the start-gate tests in test_audit_fix_w09 cover that gate.
    monkeypatch.setattr(manager, "_live_doctor_refusal", lambda *_args: None)
    _write_standing(load_config(str(manager.config_path)))
    assert manager.should_auto_start() is True
    assert _delivered("live_rearm_required") == 0

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
    manager.start_engine(auto_start=True)
    proof = captured["live_startup_proof"]
    assert isinstance(proof, LiveStartupProof)
    assert proof.authorization == "standing" and proof.account_id == "acct-live"
    assert (captured["api_key"], captured["secret_key"]) == ("AKlive", "live-secret")
    manager.stop_engine()


def test_manual_live_start_still_requires_the_interactive_arm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _live_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(manager, "_live_doctor_refusal", lambda *_args: None)  # see the standing test above
    _write_standing(load_config(str(manager.config_path)))
    monkeypatch.setattr("parayabuck.runtime.EngineRuntime", lambda **kwargs: pytest.fail("must not start"))
    with pytest.raises(ValueError, match="yeni bir canlı onay"):
        manager.start_engine()


def test_paper_auto_start_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _live_manager(tmp_path, monkeypatch, mode="paper")
    monkeypatch.delenv("PARAYABUCK_ENABLE_LIVE")
    monkeypatch.setattr(manager, "get_credentials", lambda: ("PKpaper", "paper-secret"))
    assert manager.should_auto_start() is True
    assert _delivered("live_rearm_required") == 0


def test_authorize_live_standing_days_writes_and_revoke_deletes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _live_config(tmp_path)
    monkeypatch.setattr(cli, "AlpacaBroker", _FakeLiveBroker)
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", _HMAC)
    monkeypatch.setenv("ALPACA_LIVE_API_KEY", "AKlive")
    monkeypatch.setenv("ALPACA_LIVE_SECRET_KEY", "live-secret")
    # F3-5-6: an out-of-range --standing-days is a usage error (64), not a refusal (1).
    assert cli._authorize_live(config, _authorize_args(standing_days=45)) == cli.EXIT_USAGE  # type: ignore[arg-type]
    assert "between 1 and 30" in capsys.readouterr().err
    assert cli._authorize_live(config, _authorize_args(standing_days=7)) == 0  # type: ignore[arg-type]
    path = standing_authorization_file(config)
    assert path is not None and path.exists()
    assert path.stat().st_mode & 0o077 == 0
    status = standing_authorization_status(config)
    assert status.valid and status.authorization is not None
    assert status.authorization.account_id == "acct-live"
    assert cli._authorize_live(config, _authorize_args(operator=None, revoke_standing=True)) == 0  # type: ignore[arg-type]
    assert not path.exists()


# --- mode-aware doctor + unit ----------------------------------------------


def _doctor(path: Path, capsys: pytest.CaptureFixture[str], **flags: Any) -> tuple[int, list[str]]:
    args = SimpleNamespace(config=str(path), strict=True, paper=False, configured_mode=False, offline=True)
    for key, value in flags.items():
        setattr(args, key, value)
    code = cli._run_doctor(args)  # type: ignore[arg-type]
    lines = [line for line in capsys.readouterr().out.splitlines() if line[:4] in {"PASS", "FAIL", "WARN"}]
    return code, lines


def test_configured_mode_doctor_applies_exactly_the_paper_checks_in_paper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write_settings(tmp_path, mode="paper")
    monkeypatch.setenv("ALPACA_API_KEY", "PKpaper")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "paper-secret")
    paper_code, paper_lines = _doctor(path, capsys, paper=True)
    mode_code, mode_lines = _doctor(path, capsys, configured_mode=True)
    assert paper_code == mode_code == 0
    assert set(paper_lines) <= set(mode_lines)
    assert set(mode_lines) - set(paper_lines) == {"PASS configured runtime.mode: paper"}
    monkeypatch.setenv("PARAYABUCK_ENABLE_LIVE", "YES")
    assert _doctor(path, capsys, configured_mode=True)[0] == 1


def test_configured_mode_doctor_enforces_the_live_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write_settings(tmp_path)
    monkeypatch.setenv("ALPACA_API_KEY", "PKpaper")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "paper-secret")
    code, lines = _doctor(path, capsys, configured_mode=True)
    assert code == 1
    failed = {line.split(":")[0] for line in lines if line.startswith("FAIL")}
    assert {
        "FAIL credentials present",
        "FAIL PARAYABUCK_ENABLE_LIVE=YES",
        "FAIL operator approval key >= 32 chars",
        "FAIL e-mail alerts enabled (LIVE)",
    } <= failed
    # --paper still refuses a LIVE configuration outright.
    assert _doctor(path, capsys, paper=True)[0] == 1


def test_systemd_unit_runs_the_mode_aware_strict_preflight() -> None:
    unit = (_REPO / "deploy" / "systemd" / "parayabuck.service").read_text(encoding="utf-8")
    exec_pre = [line for line in unit.splitlines() if line.startswith("ExecStartPre=")]
    assert exec_pre == ["ExecStartPre=/opt/parayabuck/.venv/bin/parayabuck doctor --strict --configured-mode --offline"]
