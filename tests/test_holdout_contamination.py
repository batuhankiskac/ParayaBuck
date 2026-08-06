from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from parayabuck.backtest.holdout_registry import (
    STATE_FAILED,
    HoldoutFingerprint,
    HoldoutLockedError,
    HoldoutRegistry,
)
from parayabuck.core.models import Bar, Market
from parayabuck.data.research_store import ProviderMetadata, VersionedResearchStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def _bar(timestamp: datetime, close: float = 100.0) -> Bar:
    return Bar(
        timestamp,
        "SPY",
        "1d",
        close * 0.999,
        close * 1.001,
        close * 0.998,
        close,
        1_000_000,
        Market.EQUITY,
    )


def _setup(
    tmp_path: Path,
) -> tuple[VersionedResearchStore, HoldoutRegistry, Path]:
    store = VersionedResearchStore(tmp_path / "research")
    start = datetime(2020, 1, 1, tzinfo=UTC)
    store.save_dataset(
        "equity_etf_1d_v1",
        [_bar(start + timedelta(days=day)) for day in range(60)],
        timeframe="1d",
        source_feed="legacy",
        adjustment="splits",
        universe_version="permanent_etf_core_v1",
    )
    registry = HoldoutRegistry(tmp_path / "registry.json")
    experiment_path = tmp_path / "equity_dual_momentum_v1.yaml"
    experiment_path.write_text(
        yaml.safe_dump(
            {
                "end": "2026-08-01T00:00:00Z",
                "outer_holdout_months": 6,
            }
        ),
        encoding="utf-8",
    )
    return store, registry, experiment_path


def test_legacy_contamination_registers_permanent_lock(tmp_path: Path) -> None:
    import run_portfolio_research as rpr

    store, registry, experiment_path = _setup(tmp_path)
    rpr._register_holdout_contamination(
        store,
        registry,
        dataset_id="equity_etf_1d_v1",
        experiment_path=experiment_path,
        experiment_note="legacy_v1_original_contaminated",
    )
    digest = HoldoutFingerprint(
        market=Market.EQUITY,
        timeframe="1d",
        universe_snapshot_hash="",
        holdout_start=datetime(2026, 2, 1, tzinfo=UTC),
        holdout_end=datetime(2026, 8, 1, tzinfo=UTC),
        adjustment="splits",
        source_provider="legacy",
    ).structural_digest()
    records = registry.records_for(digest)
    assert len(records) == 1
    assert records[0].state == STATE_FAILED
    assert "contaminated_by_full_sample_baseline" in records[0].notes
    # The contamination record is permanent: preflight refuses reopening.
    with pytest.raises(HoldoutLockedError):
        registry.preflight(
            HoldoutFingerprint(
                market=Market.EQUITY,
                timeframe="1d",
                universe_snapshot_hash="",
                holdout_start=datetime(2026, 2, 1, tzinfo=UTC),
                holdout_end=datetime(2026, 8, 1, tzinfo=UTC),
                adjustment="splits",
                source_provider="legacy",
            ),
            "future-experiment",
        )


def test_legacy_contamination_registration_is_idempotent(tmp_path: Path) -> None:
    import run_portfolio_research as rpr

    store, registry, experiment_path = _setup(tmp_path)
    for _ in range(3):
        rpr._register_holdout_contamination(
            store,
            registry,
            dataset_id="equity_etf_1d_v1",
            experiment_path=experiment_path,
            experiment_note="legacy_v1_original_contaminated",
        )
    records = registry._records  # noqa: SLF001 - test introspection
    assert len(records) == 1


def test_legacy_contamination_skipped_when_dataset_absent(tmp_path: Path) -> None:
    import run_portfolio_research as rpr

    store = VersionedResearchStore(tmp_path / "empty")
    registry = HoldoutRegistry(tmp_path / "registry.json")
    rpr._register_holdout_contamination(
        store,
        registry,
        dataset_id="equity_etf_1d_v1",
        experiment_path=tmp_path / "equity_dual_momentum_v1.yaml",
        experiment_note="legacy_v1_original_contaminated",
    )
    assert registry._records == []  # noqa: SLF001


def test_contamination_uses_provider_metadata_when_present(tmp_path: Path) -> None:
    import run_portfolio_research as rpr

    store, registry, experiment_path = _setup(tmp_path)
    store.save_dataset(
        "equity_etf_1d_v1",
        [_bar(datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=day)) for day in range(30)],
        timeframe="1d",
        source_feed="sip",
        adjustment="all",
        universe_version="permanent_etf_core_v1",
        provider_metadata=ProviderMetadata(
            provider_name="alpaca",
            source_feed="sip",
            vendor_symbol="SPY",
            adjustment_method="all",
            currency="USD",
            timezone="America/New_York",
            exchange_calendar="NYSE",
            downloaded_at=datetime(2026, 8, 5, tzinfo=UTC),
            provider_version="1.0.0",
            research_provider_differs_from_execution=False,
        ),
    )
    rpr._register_holdout_contamination(
        store,
        registry,
        dataset_id="equity_etf_1d_v1",
        experiment_path=experiment_path,
        experiment_note="legacy_v1_original_contaminated",
    )
    # With provider_metadata present, the fingerprint provider is the
    # metadata provider_name (alpaca), not the source_feed fallback.
    digest = HoldoutFingerprint(
        market=Market.EQUITY,
        timeframe="1d",
        universe_snapshot_hash="",
        holdout_start=datetime(2026, 2, 1, tzinfo=UTC),
        holdout_end=datetime(2026, 8, 1, tzinfo=UTC),
        adjustment="all",
        source_provider="alpaca",
    ).structural_digest()
    assert registry.records_for(digest)
