"""Wave-2 LIVE gate and operator approval fixes (raw-r7 S2-5 and S2-8).

Offline and deterministic: synthetic keys, tmp_path state, no broker, no network,
no production paths. The engine runtime is built against tmp_path ledgers only.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import parayabuck.config as config_module
import parayabuck.live_safety as live_safety_module
import parayabuck.trading_host as trading_host_module
from parayabuck import cli as cli_module
from parayabuck import runtime as runtime_module
from parayabuck.config import (
    DEFAULT_DECISION_DB_PATH,
    DEFAULT_LIVE_DECISION_DB_PATH,
    Config,
    LiveConfig,
    RuntimeConfig,
    RuntimeMode,
    approval_key_env_problem,
    same_ledger_path,
)
from parayabuck.live_safety import (
    LiveApprovalError,
    LiveOperatorApproval,
    StandingLiveAuthorization,
    approval_consumed_path,
    approval_nonce_consumed,
    check_approval_key,
    record_approval_consumed,
    revoke_standing_authorization,
)
from parayabuck.runtime import EngineRuntime
from parayabuck.trading_host import TRADING_HOST_ENV, TRADING_HOST_REFUSAL, trading_host_identity
from tests.helpers import make_manager

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET = "Xq7Lm2Zp9Rt4Vb6Nc8Hj1Kf3Dg5Sw0Ya"  # 32 characters, 32 distinct
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def _approval(*, host: str = "host-a", nonce: str = "approval-1") -> LiveOperatorApproval:
    return LiveOperatorApproval(
        operator_id="owner",
        account_id="acct-1",
        configuration_digests=("digest-a",),
        maximum_dollar_exposure=500.0,
        approved_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
        nonce=nonce,
        trading_host=host,
    ).signed(SECRET)


def _write_approval(path: Path, approval: LiveOperatorApproval) -> Path:
    path.write_text(json.dumps(approval.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def _standing(*, nonce: str = "standing-1") -> StandingLiveAuthorization:
    return StandingLiveAuthorization(
        operator_id="owner",
        account_id="acct-1",
        configuration_digests=("digest-a",),
        config_fingerprint="fingerprint-1",
        maximum_dollar_exposure=500.0,
        trading_host="host-a",
        approved_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(days=5),
        nonce=nonce,
    ).signed(SECRET)


def _write_standing(path: Path, authorization: StandingLiveAuthorization) -> Path:
    path.write_text(json.dumps(authorization.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def _write_settings(root: Path, raw: dict[str, Any]) -> Path:
    path = root / "settings.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# S2-5-1: only LIVE uses the live endpoint
# ---------------------------------------------------------------------------


def test_disabled_mode_never_builds_a_broker_on_the_live_endpoint(tmp_path: Path, monkeypatch: Any) -> None:
    def _no_broker(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("DISABLED must not build a broker")

    monkeypatch.setattr(runtime_module, "AlpacaBroker", _no_broker)
    config = Config(runtime=RuntimeConfig(mode=RuntimeMode.DISABLED, decision_db_path=str(tmp_path / "p.sqlite3")))

    with pytest.raises(RuntimeError, match="does not support runtime.mode=disabled"):
        EngineRuntime("key", "secret", config)


def test_live_mode_builds_the_live_endpoint(tmp_path: Path) -> None:
    config = Config(
        runtime=RuntimeConfig(mode=RuntimeMode.LIVE, decision_db_path=str(tmp_path / "live.sqlite3")),
        live=LiveConfig(enabled=True, hard_dollar_cap=100.0, operator_approval_path=str(tmp_path / "a.json")),
    )

    runtime = EngineRuntime("key", "secret", config)

    assert runtime.paper is False


# ---------------------------------------------------------------------------
# S2-5-2: ledger identity follows symlinks and relative paths
# ---------------------------------------------------------------------------


def test_symlinked_and_absolute_paths_to_the_paper_ledger_are_the_same_ledger(tmp_path: Path) -> None:
    link = tmp_path / "paper-link.sqlite3"
    link.symlink_to(os.path.abspath(DEFAULT_DECISION_DB_PATH))

    assert same_ledger_path(str(link), DEFAULT_DECISION_DB_PATH)
    assert same_ledger_path(os.path.abspath(DEFAULT_DECISION_DB_PATH), DEFAULT_DECISION_DB_PATH)
    assert not same_ledger_path(str(tmp_path / "other.sqlite3"), DEFAULT_DECISION_DB_PATH)


def test_live_mode_remaps_a_symlinked_paper_ledger_to_the_live_ledger(tmp_path: Path) -> None:
    link = tmp_path / "paper-link.sqlite3"
    link.symlink_to(os.path.abspath(DEFAULT_DECISION_DB_PATH))

    config = RuntimeConfig(mode=RuntimeMode.LIVE, decision_db_path=str(link))

    assert config.decision_db_path == DEFAULT_LIVE_DECISION_DB_PATH


# ---------------------------------------------------------------------------
# S2-5-3: a PAPER engine never starts on the LIVE ledger
# ---------------------------------------------------------------------------


def test_paper_engine_refuses_to_start_on_the_live_ledger(tmp_path: Path, monkeypatch: Any) -> None:
    live_db = tmp_path / "live.sqlite3"
    # Point the LIVE ledger at tmp_path so the test never touches data/runtime/live.
    monkeypatch.setattr(runtime_module, "DEFAULT_LIVE_DECISION_DB_PATH", str(live_db))
    monkeypatch.setenv(TRADING_HOST_ENV, "1")
    config = Config(runtime=RuntimeConfig(mode=RuntimeMode.PAPER, decision_db_path=str(live_db)))
    runtime = EngineRuntime("key", "secret", config)
    runtime._snapshot_writer = None

    with pytest.raises(RuntimeError, match="LIVE ledger"):
        runtime.start()

    assert runtime._running is False


# ---------------------------------------------------------------------------
# S2-5-4: a held broker is never polled on the wrong endpoint
# ---------------------------------------------------------------------------


def test_account_poll_refuses_a_held_broker_on_the_other_endpoint(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    manager.broker = SimpleNamespace(paper=True)

    assert manager._account_broker_for_refresh(live=True) is None


def test_broker_reports_the_endpoint_it_was_built_for() -> None:
    from parayabuck.execution.alpaca import AlpacaBroker

    assert AlpacaBroker("key", "secret", paper=False).paper is False
    assert AlpacaBroker("key", "secret", paper=True).paper is True


def test_account_poll_reuses_a_held_broker_on_the_matching_endpoint(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    held = SimpleNamespace(paper=True)
    manager.broker = held

    assert manager._account_broker_for_refresh(live=False) is held


# ---------------------------------------------------------------------------
# S2-5-5: the LIVE endpoint probe needs a config that parses as LIVE
# ---------------------------------------------------------------------------


def test_probe_gate_is_false_for_raw_live_flag_over_a_paper_config(tmp_path: Path) -> None:
    _write_settings(
        tmp_path,
        {"runtime": {"mode": "paper"}, "live": {"enabled": True, "hard_dollar_cap": 100.0}},
    )
    manager = make_manager(tmp_path)

    # The parsed config refuses live.enabled with runtime.mode=paper, so the probe stays closed.
    assert manager._parsed_config_is_live() is False


def test_probe_gate_is_false_when_the_config_does_not_parse(tmp_path: Path) -> None:
    (tmp_path / "settings.yaml").write_text("runtime: [unclosed\n", encoding="utf-8")
    manager = make_manager(tmp_path)

    assert manager._parsed_config_is_live() is False


# ---------------------------------------------------------------------------
# S2-5-6: manual approvals bind to the trading host and are used once
# ---------------------------------------------------------------------------


def test_manual_approval_is_refused_on_another_trading_host(tmp_path: Path) -> None:
    path = _write_approval(tmp_path / "approval.json", _approval(host="host-a"))

    loaded = LiveOperatorApproval.load_verified(
        path, secret=SECRET, account_id="acct-1", configuration_digests=("digest-a",), trading_host="host-a", now=NOW
    )
    assert loaded.nonce == "approval-1"
    with pytest.raises(LiveApprovalError, match="different trading host"):
        LiveOperatorApproval.load_verified(
            path,
            secret=SECRET,
            account_id="acct-1",
            configuration_digests=("digest-a",),
            trading_host="host-b",
            now=NOW,
        )


def test_approval_without_a_host_binding_is_refused_when_a_host_is_required(tmp_path: Path) -> None:
    unbound = LiveOperatorApproval(
        operator_id="owner",
        account_id="acct-1",
        configuration_digests=("digest-a",),
        maximum_dollar_exposure=500.0,
        approved_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
        nonce="old-approval",
    ).signed(SECRET)
    path = _write_approval(tmp_path / "approval.json", unbound)

    with pytest.raises(LiveApprovalError, match="different trading host"):
        LiveOperatorApproval.load_verified(
            path,
            secret=SECRET,
            account_id="acct-1",
            configuration_digests=("digest-a",),
            trading_host="host-a",
            now=NOW,
        )


def test_consumed_approval_is_recorded_once_and_survives_reads(tmp_path: Path) -> None:
    approval_path = tmp_path / "approval.json"

    assert approval_nonce_consumed(approval_path, "approval-1") is False
    record_approval_consumed(approval_path, "approval-1")
    record_approval_consumed(approval_path, "approval-1")

    assert approval_nonce_consumed(approval_path, "approval-1") is True
    assert approval_nonce_consumed(approval_path, "approval-2") is False
    assert json.loads(approval_consumed_path(approval_path).read_text(encoding="utf-8")) == {"nonces": ["approval-1"]}


def test_unreadable_consumed_record_fails_closed(tmp_path: Path) -> None:
    approval_path = tmp_path / "approval.json"
    approval_consumed_path(approval_path).write_text("not json", encoding="utf-8")

    assert approval_nonce_consumed(approval_path, "approval-1") is True


# ---------------------------------------------------------------------------
# S2-8-1: the approval key name is a key-like environment name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["PATH", "HOME", "LD_PRELOAD", "ALPACA_SECRET_KEY", "pb_operator_key", "PYTHONPATH"])
def test_non_key_or_broker_names_are_refused_as_approval_key_env(name: str) -> None:
    assert approval_key_env_problem(name) is not None


def test_key_like_names_are_accepted_and_empty_name_resolves_to_the_default() -> None:
    assert approval_key_env_problem("PARAYABUCK_OPERATOR_APPROVAL_KEY") is None
    assert approval_key_env_problem("PB_TEST_OPERATOR_KEY") is None
    with pytest.raises(ValueError, match="operator_approval_key_env"):
        LiveConfig(enabled=False, operator_approval_key_env="PATH")
    assert LiveConfig(operator_approval_key_env="").operator_approval_key_env == "PARAYABUCK_OPERATOR_APPROVAL_KEY"


# ---------------------------------------------------------------------------
# S2-8-2: key strength rejects repeated characters
# ---------------------------------------------------------------------------


def test_repeated_character_key_is_refused_and_random_key_is_accepted() -> None:
    with pytest.raises(ValueError, match="distinct characters"):
        check_approval_key("k" * 32)
    check_approval_key(SECRET)


# ---------------------------------------------------------------------------
# S2-8-4: revoking a standing authorization holds for restored copies
# ---------------------------------------------------------------------------


def test_revoked_standing_authorization_stays_refused_when_restored(tmp_path: Path) -> None:
    path = _write_standing(tmp_path / "live_standing_authorization.json", _standing(nonce="standing-1"))
    original_bytes = path.read_bytes()

    assert revoke_standing_authorization(path) is True
    assert not path.exists()
    assert revoke_standing_authorization(path) is False

    path.write_bytes(original_bytes)  # a restored backup copy
    with pytest.raises(LiveApprovalError, match="revoked"):
        StandingLiveAuthorization.read_signed(path, secret=SECRET)


def test_new_standing_authorization_after_revocation_is_accepted(tmp_path: Path) -> None:
    path = _write_standing(tmp_path / "live_standing_authorization.json", _standing(nonce="standing-1"))
    revoke_standing_authorization(path)

    _write_standing(path, _standing(nonce="standing-2"))

    assert StandingLiveAuthorization.read_signed(path, secret=SECRET).nonce == "standing-2"


# ---------------------------------------------------------------------------
# S2-8-5: the start path refuses non-regular, symlinked and oversized documents
# ---------------------------------------------------------------------------


def test_fifo_approval_is_refused_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "approval.json"
    os.mkfifo(fifo)

    with pytest.raises(LiveApprovalError):
        LiveOperatorApproval.load_verified(
            fifo, secret=SECRET, account_id="acct-1", configuration_digests=("digest-a",), now=NOW
        )


def test_symlinked_and_oversized_approval_documents_are_refused(tmp_path: Path) -> None:
    real = _write_approval(tmp_path / "real.json", _approval())
    link = tmp_path / "link.json"
    link.symlink_to(real)
    big = tmp_path / "big.json"
    big.write_text("x" * (64 * 1024 + 10), encoding="utf-8")

    for candidate in (link, big):
        with pytest.raises(LiveApprovalError):
            LiveOperatorApproval.load_verified(
                candidate,
                secret=SECRET,
                account_id="acct-1",
                configuration_digests=("digest-a",),
                now=NOW,
            )


# ---------------------------------------------------------------------------
# S2-8-6: authorize-live refuses an unusable key before the LIVE probe
# ---------------------------------------------------------------------------


def test_authorize_live_refuses_the_placeholder_key_before_the_live_probe(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", "replace_with_at_least_32_random_characters")
    monkeypatch.setattr(config_module, "load_env_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(live_safety_module, "mode_credentials", lambda *, live, env=None: ("AKTEST", "secret-value"))

    def _no_probe(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the LIVE endpoint must not be probed with an unusable key")

    monkeypatch.setattr(cli_module, "AlpacaBroker", _no_probe)
    config = Config(
        runtime=RuntimeConfig(mode=RuntimeMode.LIVE, decision_db_path=str(tmp_path / "live.sqlite3")),
        live=LiveConfig(
            enabled=True,
            hard_dollar_cap=100.0,
            operator_approval_path=str(tmp_path / "approval.json"),
        ),
    )
    args = SimpleNamespace(operator="owner", expires_minutes=15, standing_days=None, revoke_standing=False)

    with pytest.raises(SystemExit, match="PARAYABUCK_OPERATOR_APPROVAL_KEY"):
        cli_module._authorize_live(config, args)


# ---------------------------------------------------------------------------
# S2-8-7: the host identity never falls back to the hostname alone
# ---------------------------------------------------------------------------


def test_host_identity_fails_closed_without_a_machine_id(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(trading_host_module, "_MACHINE_ID_PATHS", (str(tmp_path / "missing-machine-id"),))

    with pytest.raises(ValueError, match="machine id"):
        trading_host_identity()


def test_host_identity_is_stable_with_a_machine_id(monkeypatch: Any, tmp_path: Path) -> None:
    machine_id = tmp_path / "machine-id"
    machine_id.write_text("0123456789abcdef0123456789abcdef\n", encoding="utf-8")
    monkeypatch.setattr(trading_host_module, "_MACHINE_ID_PATHS", (str(machine_id),))

    first = trading_host_identity()

    assert first == trading_host_identity()
    assert len(first) == 32


# ---------------------------------------------------------------------------
# S2-8-8: the approval key value is redacted from error text
# ---------------------------------------------------------------------------


def test_approval_key_value_is_redacted_from_error_text(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("PARAYABUCK_OPERATOR_APPROVAL_KEY", SECRET)
    manager = make_manager(tmp_path)

    text = manager.redact_secrets(f"approval failed for key {SECRET}")

    assert SECRET not in text
    assert "[redacted]" in text


# ---------------------------------------------------------------------------
# Trading-host refusal text names the drop-in that sets the marker
# ---------------------------------------------------------------------------


def test_trading_host_refusal_names_the_dropin_that_sets_the_marker() -> None:
    dropin = REPO_ROOT / "deploy" / "systemd" / "vm-dropin" / "trading-host.conf"

    assert "deploy/systemd/vm-dropin/trading-host.conf" in TRADING_HOST_REFUSAL
    assert f"{TRADING_HOST_ENV}=1" in TRADING_HOST_REFUSAL
    assert f"Environment={TRADING_HOST_ENV}=1" in dropin.read_text(encoding="utf-8")
