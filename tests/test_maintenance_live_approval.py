from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from parayabuck.live_safety import LiveApprovalError, LiveOperatorApproval

# Synthetic unit-test identities only. No runtime or broker is constructed.
SECRET = "offline-test-key-not-a-credential-0000"
START = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


def _approval() -> LiveOperatorApproval:
    return LiveOperatorApproval(
        operator_id="test-operator",
        account_id="test-account",
        configuration_digests=("test-strategy", "test-risk"),
        maximum_dollar_exposure=100.0,
        approved_at=START,
        expires_at=START + timedelta(minutes=15),
        nonce="offline-nonce",
        config_fingerprint="fp-test",
    )


@pytest.mark.parametrize("cap", [float("nan"), float("inf"), -float("inf"), 0.0, -1.0])
def test_approval_rejects_nonfinite_or_nonpositive_cap(cap: float) -> None:
    with pytest.raises(ValueError, match="dollar cap"):
        replace(_approval(), maximum_dollar_exposure=cap)


@pytest.mark.parametrize("cap", [0.01, 100.0, 1_000_000.0])
def test_approval_accepts_positive_finite_cap(cap: float) -> None:
    assert replace(_approval(), maximum_dollar_exposure=cap).maximum_dollar_exposure == cap


@pytest.mark.parametrize(
    ("offset", "valid"),
    [
        (timedelta(microseconds=-1), False),
        (timedelta(), True),
        (timedelta(minutes=15, microseconds=-1), True),
        (timedelta(minutes=15), False),
        (timedelta(minutes=16), False),
    ],
)
def test_verified_approval_enforces_both_time_bounds(tmp_path: Path, offset: timedelta, valid: bool) -> None:
    approval = _approval().signed(SECRET)
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval.to_dict()), encoding="utf-8")
    arguments = {
        "secret": SECRET,
        "account_id": approval.account_id,
        "configuration_digests": approval.configuration_digests,
        "config_fingerprint": "fp-test",
        "now": START + offset,
    }
    if valid:
        assert LiveOperatorApproval.load_verified(path, **arguments) == approval
    else:
        with pytest.raises(LiveApprovalError):
            LiveOperatorApproval.load_verified(path, **arguments)


def test_approval_time_bound_compares_instants_across_offsets(tmp_path: Path) -> None:
    approval = replace(
        _approval(),
        approved_at=START.astimezone(timezone(timedelta(hours=3))),
        expires_at=(START + timedelta(minutes=15)).astimezone(timezone(timedelta(hours=3))),
    ).signed(SECRET)
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval.to_dict()), encoding="utf-8")
    loaded = LiveOperatorApproval.load_verified(
        path,
        secret=SECRET,
        account_id=approval.account_id,
        configuration_digests=approval.configuration_digests,
        config_fingerprint="fp-test",
        now=START,
    )
    assert loaded.approved_at == START


@pytest.mark.parametrize("field", ["signature", "account_id", "configuration_digests"])
def test_signed_approval_binding_remains_fail_closed(tmp_path: Path, field: str) -> None:
    approval = _approval().signed(SECRET)
    payload = approval.to_dict()
    if field == "signature":
        payload[field] = "0" * 64
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LiveApprovalError):
        LiveOperatorApproval.load_verified(
            path,
            secret=SECRET,
            account_id="wrong-account" if field == "account_id" else approval.account_id,
            configuration_digests=("wrong-digest",)
            if field == "configuration_digests"
            else approval.configuration_digests,
            config_fingerprint="fp-test",
            now=START,
        )
