"""Research tiers: smoke / quick / robustness / canonical."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from parayabuck.core.models import Market

from .models import ResearchTier


@dataclass(frozen=True)
class TierSpec:
    tier: ResearchTier
    description: str
    # If start/end None, use job's explicit window; otherwise tier provides defaults.
    default_start: datetime | None
    default_end: datetime | None
    synthetic: bool
    allow_full_dataset: bool
    default_persist_artifacts: bool
    max_trials_recommended: int | None


# Canonical quick window is the same as benchmarks/bench_backtest.py and parity.py.
# Keep it explicit so agents cannot silently drift.
_QUICK_START = datetime(2019, 1, 1, tzinfo=UTC)
_QUICK_END = datetime(2021, 12, 31, tzinfo=UTC)


TIER_SPECS: dict[ResearchTier, TierSpec] = {
    ResearchTier.SMOKE: TierSpec(
        tier=ResearchTier.SMOKE,
        description="Smoke: tiny synthetic data, seconds, catch crashes. No economic conclusions.",
        default_start=None,
        default_end=None,
        synthetic=True,
        allow_full_dataset=False,
        default_persist_artifacts=False,
        max_trials_recommended=4,
    ),
    ResearchTier.QUICK: TierSpec(
        tier=ResearchTier.QUICK,
        description="Quick falsification: 2019-2021 equity window (or equivalent). Reject bad ideas fast.",
        default_start=_QUICK_START,
        default_end=_QUICK_END,
        synthetic=False,
        allow_full_dataset=False,
        default_persist_artifacts=False,
        max_trials_recommended=12,
    ),
    ResearchTier.ROBUSTNESS: TierSpec(
        tier=ResearchTier.ROBUSTNESS,
        description="Robustness: subperiods, cost stress, limited sensitivity. For survivors of quick.",
        default_start=None,
        default_end=None,
        synthetic=False,
        allow_full_dataset=False,
        default_persist_artifacts=False,
        max_trials_recommended=32,
    ),
    ResearchTier.CANONICAL: TierSpec(
        tier=ResearchTier.CANONICAL,
        description="Canonical: full PIT dataset, full period, CA, cost stress, walk-forward, full artifacts. Final gate only.",
        default_start=None,
        default_end=None,
        synthetic=False,
        allow_full_dataset=True,
        default_persist_artifacts=True,
        max_trials_recommended=None,
    ),
}


def tier_spec(tier: ResearchTier) -> TierSpec:
    return TIER_SPECS[tier]


def resolve_window(
    tier: ResearchTier,
    explicit_start: datetime | None,
    explicit_end: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    """Tier provides defaults only when caller did not specify explicit window."""
    spec = TIER_SPECS[tier]
    if tier == ResearchTier.SMOKE:
        # Smoke uses caller-provided synthetic bars; no default window.
        return explicit_start, explicit_end
    if tier == ResearchTier.QUICK:
        # Quick defaults to 2019-2021 if not specified.
        return explicit_start or spec.default_start, explicit_end or spec.default_end
    # Robustness and canonical: caller must be explicit; no silent default.
    return explicit_start, explicit_end


def default_dataset_for_tier(tier: ResearchTier, market: Market) -> Path | None:
    """Suggest dataset path for tier; caller may override via job.dataset_path."""
    root = Path(__file__).resolve().parents[3]
    if tier == ResearchTier.SMOKE:
        return None
    if market == Market.CRYPTO:
        # Use extended crypto path as canonical; quick uses same file but with window.
        return root / "data" / "historical" / "crypto_18_1d_extended.csv"
    # Equity: quick uses 1200 sym file; canonical uses PIT symmetric.
    if tier == ResearchTier.CANONICAL:
        return root / "data" / "historical" / "equity_pit_symmetric_2016_2026_v5.csv"
    return root / "data" / "historical" / "equity_1200_2016_2026.csv"
