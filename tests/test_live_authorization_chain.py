"""End-to-end LIVE authorization chain, verified offline at the runtime gate.

A LIVE start is authorized by one chain of links: the operator's HMAC-signed
approval (bound to the account, the configuration digests, the whole settings
fingerprint, the trading host and a validity window), the startup proof the
web arm hands the runtime (bound to the credential fingerprint and the same
digests), the broker account the key resolves to, and the paper/live account
classification. ``EngineRuntime._verify_live_startup_proof`` is the gate every
LIVE start passes before any broker mutation.

These tests build one fully valid fake chain, accept it, and then break each
link in turn and assert the refusal. Everything is synthetic and deterministic:
credentials are fake, the approval key is a test value, the host identity is
pinned, every file lives under ``tmp_path``, and the broker is a spy that only
allows the read-only account reads the verifier makes.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from parayabuck import runtime as runtime_module
from parayabuck.config import Config, load_config
from parayabuck.live_authorization import live_configuration_digests
from parayabuck.live_safety import (
    LiveApprovalError,
    LiveOperatorApproval,
    LiveStartupProof,
    approval_nonce_consumed,
    config_fingerprint,
    record_approval_consumed,
)
from parayabuck.runtime import EngineRuntime

_REPO = Path(__file__).resolve().parents[1]
_KEY = "0123456789abcdef0123456789abcdef01234567"  # the operator's real-looking test key
_OTHER_KEY = "fedcba9876543210fedcba9876543210fedcba98"  # signs a forged approval
_HOST = "host-a"
_ACCOUNT = "acct-live"
_ACCOUNT_NUMBER = "912345678"
_API_KEY = "AKlive"
_SECRET = "live-secret"
_NOW = datetime.now(UTC)

# Read-only reads the verifier may make on the broker: the account snapshot and
# the identity attributes it compares. Anything else is a mutation or an
# unexpected call, and fails the test.
_ALLOWED_BROKER_READS = frozenset({"account", "account_id", "account_number"})


class _ChainBroker:
    """Spy broker: records every attribute the runtime touches."""

    def __init__(self, *, account_id: str, account_number: str) -> None:
        self.account_id = account_id
        self.account_number = account_number
        self.touched: list[str] = []

    def account(self) -> object:
        self.touched.append("account")
        return object()

    def __getattr__(self, name: str) -> Any:
        # Only reached for names the spy does not define: record it, then report it absent.
        self.touched.append(name)
        raise AttributeError(name)

    def unexpected_reads(self) -> set[str]:
        return set(self.touched) - _ALLOWED_BROKER_READS


@dataclass
class _Chain:
    config: Config
    approval_path: Path
    broker: _ChainBroker
    runtime: EngineRuntime
    digests: tuple[str, ...]


@dataclass
class _Breaks:
    """One link broken at a time: overrides applied on top of the valid chain."""

    approval: dict[str, Any] = field(default_factory=dict)
    approval_key: str = _KEY
    approval_signed: bool = True
    proof: dict[str, Any] = field(default_factory=dict)
    broker: dict[str, Any] = field(default_factory=dict)
    cross_check: bool = True
    consumed: bool = False


def _settings(tmp_path: Path) -> Path:
    """Shipped settings, LIVE mode, every state path under ``tmp_path``."""
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
            "hard_dollar_cap": 1000.0,
            "operator_approval_path": str(tmp_path / "live_approval.json"),
        }
    )
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def _credential_fingerprint(api_key: str = _API_KEY, secret: str = _SECRET) -> str:
    # The same derivation the engine uses to issue the proof and the runtime uses to check it.
    return hashlib.sha256(f"{api_key}\0{secret}".encode()).hexdigest()


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, breaks: _Breaks | None = None) -> _Chain:
    """Assemble the chain, with ``breaks`` applied to the links it names."""
    breaks = breaks or _Breaks()
    monkeypatch.setattr(runtime_module, "trading_host_identity", lambda: _HOST)
    config = load_config(str(_settings(tmp_path)))
    if not breaks.cross_check:
        config = dataclasses.replace(
            config, signal_cross_check=dataclasses.replace(config.signal_cross_check, enabled=False)
        )
    digests = live_configuration_digests(config)
    monkeypatch.setenv(config.live.operator_approval_key_env, _KEY)

    now = _NOW
    approval_fields: dict[str, Any] = {
        "operator_id": "owner",
        "account_id": _ACCOUNT,
        "configuration_digests": digests,
        "maximum_dollar_exposure": 1000.0,
        "approved_at": now - timedelta(minutes=1),
        "expires_at": now + timedelta(minutes=10),
        "nonce": "nonce-valid-1",
        "config_fingerprint": config_fingerprint(config),
        "trading_host": _HOST,
    }
    approval_fields.update(breaks.approval)
    approval = LiveOperatorApproval(**approval_fields)
    signed = approval.signed(breaks.approval_key) if breaks.approval_signed else approval
    approval_path = Path(config.live.operator_approval_path)
    approval_path.write_text(json.dumps(signed.to_dict()), encoding="utf-8")
    if breaks.consumed:
        record_approval_consumed(approval_path, approval_fields["nonce"])

    proof_fields: dict[str, Any] = {
        "account_id": _ACCOUNT,
        "credential_fingerprint": _credential_fingerprint(),
        "configuration_digests": digests,
        "issued_at": now - timedelta(seconds=1),
        "expires_at": now + timedelta(minutes=5),
        "authorization": "operator_approval",
    }
    proof_fields.update(breaks.proof)
    proof = LiveStartupProof(**proof_fields)

    broker_fields: dict[str, Any] = {"account_id": _ACCOUNT, "account_number": _ACCOUNT_NUMBER}
    broker_fields.update(breaks.broker)
    broker = _ChainBroker(**broker_fields)

    runtime = EngineRuntime.__new__(EngineRuntime)
    runtime.api_key = _API_KEY
    runtime.secret_key = _SECRET
    runtime.broker = broker
    runtime.app_config = config
    runtime._live_startup_proof = proof
    return _Chain(config=config, approval_path=approval_path, broker=broker, runtime=runtime, digests=digests)


def _verify(chain: _Chain) -> object:
    return chain.runtime._verify_live_startup_proof(chain.digests)


# ---------------------------------------------------------------------------
# The fully valid chain is accepted, and nothing beyond the read-only checks happens.
# ---------------------------------------------------------------------------


def test_fully_valid_chain_is_accepted_and_reaches_no_broker_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = _build(tmp_path, monkeypatch)

    approval = _verify(chain)

    assert isinstance(approval, LiveOperatorApproval)
    assert approval.nonce == "nonce-valid-1"
    # The nonce is only pending: it is recorded after the start has committed.
    assert chain.runtime._pending_approval_consumption == (str(chain.approval_path), "nonce-valid-1")
    assert chain.broker.unexpected_reads() == set()
    assert chain.broker.touched.count("account") == 1


# ---------------------------------------------------------------------------
# Each broken link is refused with its own reason.
# ---------------------------------------------------------------------------

_REFUSALS: list[tuple[str, Callable[[], _Breaks], type[Exception], str]] = [
    # --- the startup proof (issued by the web arm) -------------------------
    (
        "proof_expired",
        lambda: _Breaks(proof={"issued_at": _NOW - timedelta(minutes=10), "expires_at": _NOW - timedelta(minutes=5)}),
        RuntimeError,
        "missing or expired",
    ),
    (
        "proof_for_other_credentials",
        lambda: _Breaks(proof={"credential_fingerprint": _credential_fingerprint("AKother", "other-secret")}),
        RuntimeError,
        "does not match the configured credentials",
    ),
    (
        "proof_for_other_configuration",
        lambda: _Breaks(proof={"configuration_digests": ("0" * 64,)}),
        RuntimeError,
        "does not match the loaded configuration",
    ),
    (
        "proof_for_other_account",
        lambda: _Breaks(proof={"account_id": "acct-other"}),
        LiveApprovalError,
        "account does not match",
    ),
    (
        "cross_check_disabled",
        lambda: _Breaks(cross_check=False),
        RuntimeError,
        "signal_cross_check.enabled is false",
    ),
    # --- the operator's HMAC approval -----------------------------------------
    (
        "approval_signed_with_another_key",
        lambda: _Breaks(approval_key=_OTHER_KEY),
        LiveApprovalError,
        "signature is invalid",
    ),
    (
        "approval_unsigned",
        lambda: _Breaks(approval_signed=False),
        LiveApprovalError,
        "signature is invalid",
    ),
    (
        "approval_for_other_account",
        lambda: _Breaks(approval={"account_id": "acct-other"}),
        LiveApprovalError,
        "account does not match",
    ),
    (
        "approval_for_other_configuration_digests",
        lambda: _Breaks(approval={"configuration_digests": ("0" * 64,)}),
        LiveApprovalError,
        "configuration does not match",
    ),
    (
        "approval_for_other_settings_fingerprint",
        lambda: _Breaks(approval={"config_fingerprint": "f" * 64}),
        LiveApprovalError,
        "does not match the current settings",
    ),
    (
        "approval_for_another_trading_host",
        lambda: _Breaks(approval={"trading_host": "host-b"}),
        LiveApprovalError,
        "different trading host",
    ),
    (
        "approval_expired",
        lambda: _Breaks(
            approval={"approved_at": _NOW - timedelta(minutes=20), "expires_at": _NOW - timedelta(minutes=10)}
        ),
        LiveApprovalError,
        "has expired",
    ),
    (
        "approval_not_yet_valid",
        lambda: _Breaks(
            approval={"approved_at": _NOW + timedelta(minutes=10), "expires_at": _NOW + timedelta(minutes=20)}
        ),
        LiveApprovalError,
        "not yet valid",
    ),
    (
        "approval_already_used_for_a_live_start",
        lambda: _Breaks(consumed=True),
        RuntimeError,
        "already used for a LIVE start",
    ),
    # --- the broker account the key resolves to --------------------------------
    (
        "broker_account_is_not_the_proof_account",
        lambda: _Breaks(broker={"account_id": "acct-other"}),
        RuntimeError,
        "does not match the verified broker account",
    ),
    (
        "live_key_resolves_to_a_paper_account",
        lambda: _Breaks(broker={"account_number": "PA3PAPER"}),
        LiveApprovalError,
        "does not resolve to a live",
    ),
]


@pytest.mark.parametrize(("link", "make_breaks", "error", "reason"), _REFUSALS, ids=[row[0] for row in _REFUSALS])
def test_each_broken_link_refuses_the_live_start(
    link: str,
    make_breaks: Callable[[], _Breaks],
    error: type[Exception],
    reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = _build(tmp_path, monkeypatch, make_breaks())

    with pytest.raises(error, match=reason):
        _verify(chain)

    # The refusal never reaches a broker mutation: only the read-only reads are allowed.
    assert chain.broker.unexpected_reads() == set(), link
    # A refused start never records its approval as used. The only nonce on file
    # before the call is the one the already-used case seeded itself.
    seeded = link == "approval_already_used_for_a_live_start"
    assert approval_nonce_consumed(chain.approval_path, "nonce-valid-1") is seeded, link
