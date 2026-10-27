from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.live_safety import LiveApprovalError, LiveOperatorApproval


def _approval(now: datetime, *, exposure: float = 500.0) -> LiveOperatorApproval:
    return LiveOperatorApproval(
        operator_id="operator",
        account_id="live-account",
        configuration_digests=("config-a",),
        maximum_dollar_exposure=exposure,
        approved_at=now,
        expires_at=now + timedelta(minutes=15),
        nonce="nonce",
        config_fingerprint="fp-test",
    )


@pytest.mark.parametrize("exposure", [float("nan"), float("inf"), -float("inf"), 0.0, -1.0])
def test_live_approval_requires_finite_positive_exposure(exposure: float) -> None:
    with pytest.raises(ValueError, match="positive dollar cap"):
        _approval(datetime(2026, 8, 9, tzinfo=UTC), exposure=exposure)


def test_live_approval_is_bound_to_account_configuration_and_cap(tmp_path) -> None:
    now = datetime(2026, 8, 9, tzinfo=UTC)
    secret = "0123456789abcdef0123456789abcdef"
    approval = _approval(now).signed(secret)
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval.to_dict()), encoding="utf-8")

    loaded = LiveOperatorApproval.load_verified(
        path,
        secret=secret,
        account_id="live-account",
        configuration_digests=("config-a",),
        config_fingerprint="fp-test",
        now=now,
    )
    assert loaded.maximum_dollar_exposure == 500

    with pytest.raises(LiveApprovalError, match="configuration"):
        LiveOperatorApproval.load_verified(
            path,
            secret=secret,
            account_id="live-account",
            configuration_digests=("changed",),
            config_fingerprint="fp-test",
            now=now,
        )


@pytest.mark.parametrize(
    ("offset", "error"),
    [
        (timedelta(microseconds=-1), "not yet valid"),
        (timedelta(minutes=15), "expired"),
    ],
)
def test_live_approval_rejects_outside_validity_window(tmp_path, offset: timedelta, error: str) -> None:
    approved_at = datetime(2026, 8, 9, tzinfo=UTC)
    secret = "0123456789abcdef0123456789abcdef"
    approval = _approval(approved_at).signed(secret)
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval.to_dict()), encoding="utf-8")

    with pytest.raises(LiveApprovalError, match=error):
        LiveOperatorApproval.load_verified(
            path,
            secret=secret,
            account_id=approval.account_id,
            configuration_digests=approval.configuration_digests,
            config_fingerprint="fp-test",
            now=approved_at + offset,
        )


def test_live_approval_accepts_at_approval_boundary(tmp_path) -> None:
    approved_at = datetime(2026, 8, 9, tzinfo=UTC)
    secret = "0123456789abcdef0123456789abcdef"
    approval = _approval(approved_at).signed(secret)
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval.to_dict()), encoding="utf-8")

    loaded = LiveOperatorApproval.load_verified(
        path,
        secret=secret,
        account_id=approval.account_id,
        configuration_digests=approval.configuration_digests,
        config_fingerprint="fp-test",
        now=approved_at,
    )

    assert loaded == approval


def test_live_approval_rejects_expiry_and_bad_signature(tmp_path) -> None:
    now = datetime(2026, 8, 9, tzinfo=UTC)
    approval = LiveOperatorApproval(
        operator_id="operator",
        account_id="account",
        configuration_digests=("digest",),
        maximum_dollar_exposure=100,
        approved_at=now - timedelta(hours=1),
        expires_at=now - timedelta(minutes=1),
        nonce="nonce",
        config_fingerprint="fp-test",
    ).signed("0123456789abcdef0123456789abcdef")
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval.to_dict()), encoding="utf-8")

    with pytest.raises(LiveApprovalError, match="expired"):
        LiveOperatorApproval.load_verified(
            path,
            secret="0123456789abcdef0123456789abcdef",
            account_id="account",
            configuration_digests=("digest",),
            config_fingerprint="fp-test",
            now=now,
        )
    with pytest.raises(LiveApprovalError, match="signature"):
        LiveOperatorApproval.load_verified(
            path,
            secret="fedcba9876543210fedcba9876543210",
            account_id="account",
            configuration_digests=("digest",),
            config_fingerprint="fp-test",
            now=now - timedelta(minutes=30),
        )
