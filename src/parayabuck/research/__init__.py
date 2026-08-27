"""Research execution layer."""

from .cache import cache_stats, load_cached_bars, load_cached_universe, store_cached_bars, store_cached_universe
from .models import ResearchBatchResult, ResearchJob, ResearchTier, ResearchTrial, TrialResult
from .runner import ResearchRunner
from .tiers import TIER_SPECS, TierSpec, default_dataset_for_tier, resolve_window, tier_spec

__all__ = [
    "ResearchBatchResult",
    "ResearchJob",
    "ResearchTier",
    "ResearchTrial",
    "TrialResult",
    "ResearchRunner",
    "TIER_SPECS",
    "TierSpec",
    "default_dataset_for_tier",
    "resolve_window",
    "tier_spec",
    "cache_stats",
    "load_cached_bars",
    "load_cached_universe",
    "store_cached_bars",
    "store_cached_universe",
]
