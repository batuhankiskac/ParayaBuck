from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from parayabuck.core.models import Market
from parayabuck.portfolio.approval import (
    ApprovalError,
    ApprovalStage,
    LiveOperatorApproval,
    StrategyApprovalManifest,
    module_sha256,
)
from parayabuck.portfolio.equity_covariance_trend import EquityCovarianceTrend1dV1


def _manifest() -> StrategyApprovalManifest:
    now = datetime(2026, 8, 8, tzinfo=UTC)
    return StrategyApprovalManifest(
        strategy_name=EquityCovarianceTrend1dV1.name,
        strategy_version=EquityCovarianceTrend1dV1.version,
        import_path=("parayabuck.portfolio.equity_covariance_trend:EquityCovarianceTrend1dV1"),
        module_sha256=module_sha256(EquityCovarianceTrend1dV1),
        parameters={
            "trend_window": 200,
            "covariance_window": 60,
            "annual_volatility_target": 0.08,
            "max_single_weight": 0.25,
        },
        market=Market.EQUITY,
        symbols=("SPY", "QQQ", "IWM", "IEF", "SHY", "GLD"),
        maximum_allocation=0.50,
        gate_policy_version="portfolio-conservative-v1",
        dataset_ids=("equity-dual-series-v1",),
        dataset_hashes=("dataset-hash",),
        validation_record_ids=("validation-1",),
        holdout_record_ids=("holdout-1",),
        source_revision="abc123",
        approved_at=now,
        expires_at=now + timedelta(days=30),
        approved_stage=ApprovalStage.PAPER,
    ).signed()


def _write(tmp_path, manifest: StrategyApprovalManifest):
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    return path


def test_signed_manifest_loads_and_instantiates_exact_strategy(tmp_path) -> None:
    manifest = _manifest()
    loaded = StrategyApprovalManifest.load_verified(
        _write(tmp_path, manifest),
        expected_checksum=manifest.checksum,
        current_source_revision="abc123",
        now=datetime(2026, 8, 9, tzinfo=UTC),
    )
    assert isinstance(loaded.instantiate_strategy(), EquityCovarianceTrend1dV1)


def test_manifest_tamper_source_mismatch_and_expiry_fail_closed(tmp_path) -> None:
    manifest = _manifest()
    path = _write(tmp_path, manifest)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["maximum_allocation"] = 0.60
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ApprovalError, match="modified"):
        StrategyApprovalManifest.load_verified(
            path,
            expected_checksum=manifest.checksum,
            current_source_revision="abc123",
            now=datetime(2026, 8, 9, tzinfo=UTC),
        )
    clean = _write(tmp_path, manifest)
    with pytest.raises(ApprovalError, match="source revision"):
        StrategyApprovalManifest.load_verified(
            clean,
            expected_checksum=manifest.checksum,
            current_source_revision="different",
            now=datetime(2026, 8, 9, tzinfo=UTC),
        )
    with pytest.raises(ApprovalError, match="expired"):
        StrategyApprovalManifest.load_verified(
            clean,
            expected_checksum=manifest.checksum,
            current_source_revision="abc123",
            now=datetime(2026, 9, 8, tzinfo=UTC),
        )


def test_non_shadow_stage_requires_holdout_evidence() -> None:
    with pytest.raises(ValueError, match="holdout"):
        StrategyApprovalManifest(
            **{
                **_manifest().unsigned_dict(),
                "market": Market.EQUITY,
                "approved_stage": ApprovalStage.PAPER,
                "approved_at": datetime(2026, 8, 8, tzinfo=UTC),
                "expires_at": datetime(2026, 9, 8, tzinfo=UTC),
                "holdout_record_ids": (),
            }
        )


def test_live_operator_approval_is_hmac_bound_to_account_manifest_and_cap(
    tmp_path,
) -> None:
    now = datetime(2026, 8, 8, tzinfo=UTC)
    secret = "x" * 32
    approval = LiveOperatorApproval(
        operator_id="operator-1",
        account_id="paper-account",
        manifest_checksums=("manifest-a",),
        maximum_dollar_exposure=1_000,
        approved_at=now,
        expires_at=now + timedelta(days=1),
        nonce="single-use-audit-nonce",
    ).signed(secret)
    path = tmp_path / "operator.json"
    path.write_text(json.dumps(approval.to_dict()), encoding="utf-8")
    loaded = LiveOperatorApproval.load_verified(
        path,
        secret=secret,
        account_id="paper-account",
        manifest_checksums=("manifest-a",),
        now=now,
    )
    assert loaded.maximum_dollar_exposure == 1_000
    with pytest.raises(ApprovalError, match="account"):
        LiveOperatorApproval.load_verified(
            path,
            secret=secret,
            account_id="different-account",
            manifest_checksums=("manifest-a",),
            now=now,
        )
