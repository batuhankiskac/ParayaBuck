from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.live_safety import LiveApprovalError, LiveOperatorApproval


def test_live_approval_is_bound_to_account_configuration_and_cap(tmp_path) -> None:
    now = datetime(2026, 8, 9, tzinfo=UTC)
    secret = "x" * 32
    approval = LiveOperatorApproval(
        operator_id="operator",
        account_id="live-account",
        configuration_digests=("config-a",),
        maximum_dollar_exposure=500.0,
        approved_at=now,
        expires_at=now + timedelta(minutes=15),
        nonce="nonce",
    ).signed(secret)
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval.to_dict()), encoding="utf-8")

    loaded = LiveOperatorApproval.load_verified(
        path,
        secret=secret,
        account_id="live-account",
        configuration_digests=("config-a",),
        now=now,
    )
    assert loaded.maximum_dollar_exposure == 500

    with pytest.raises(LiveApprovalError, match="configuration"):
        LiveOperatorApproval.load_verified(
            path,
            secret=secret,
            account_id="live-account",
            configuration_digests=("changed",),
            now=now,
        )


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
    ).signed("a" * 32)
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval.to_dict()), encoding="utf-8")

    with pytest.raises(LiveApprovalError, match="expired"):
        LiveOperatorApproval.load_verified(
            path,
            secret="a" * 32,
            account_id="account",
            configuration_digests=("digest",),
            now=now,
        )
    with pytest.raises(LiveApprovalError, match="signature"):
        LiveOperatorApproval.load_verified(
            path,
            secret="b" * 32,
            account_id="account",
            configuration_digests=("digest",),
            now=now - timedelta(minutes=30),
        )
