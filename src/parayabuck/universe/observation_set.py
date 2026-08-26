"""Strategy-alpha observation-set builder.

The stream limit is NOT the opportunity universe: the scanner produces the
eligible pool (tradability-first), the strategy's alpha ranks the pool on
batch historical data, and the top ``maximum_stream_symbols`` plus pinned
symbols become the streamed OBSERVATION SET. Managed positions and open
managed orders stay pinned regardless of ranking.

Research-free runtime path: the builder is pure code, no manifests.

Fail-closed universe note: :func:`build_observation_set` ranks the *pool*
itself (``pool_symbols``) and intentionally builds a permissive
``PortfolioContext`` with ``universe=None`` / ``universe_version=""``.
That context is a *static pool ranking* — membership is already fixed by
the caller-supplied pool — and therefore explicitly opts into
``allow_static_context=True`` via :func:`require_product_universe`.
Dynamic product allocation decisions (``PortfolioBacktestEngine``,
``PortfolioRuntime``) MUST NOT use this permissive mode; they call
``require_product_universe`` without the opt-in and fail closed when the
PIT universe is missing, lacks ``contains()``, or carries an empty version.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy, require_product_universe


@dataclass(frozen=True)
class ObservationSetResult:
    observation_set: tuple[str, ...]
    ranked_pool: tuple[str, ...]
    alpha_scored_count: int
    strategy_name: str
    source: str  # "strategy_alpha" | "scanner_quality_fallback" | "no_ranker"


def build_observation_set(
    strategy: PortfolioStrategy | None,
    history: dict[str, tuple[Bar, ...]],
    pool_symbols: tuple[str, ...],
    *,
    market: Market,
    timeframe: str,
    decision_timestamp: datetime,
    maximum_stream_symbols: int,
    pinned_symbols: tuple[str, ...] = (),
    scanner_fallback: tuple[str, ...] = (),
) -> ObservationSetResult:
    """Rank the eligible pool with the strategy's alpha and truncate to the
    streamed observation set.

    ``pinned_symbols`` (references, inverse hedges, configured pins) are
    force-kept. ``scanner_fallback`` fills remaining slots when the strategy
    ranks fewer names than the stream capacity (e.g. young symbols without
    the strategy's warmup history).
    """
    if maximum_stream_symbols <= 0:
        raise ValueError("maximum_stream_symbols must be positive")
    if decision_timestamp.tzinfo is None:
        raise ValueError("Observation-set decision timestamps must be timezone-aware")
    reserved = tuple(dict.fromkeys(canonical_symbol(symbol, market) for symbol in pinned_symbols))
    if len(reserved) > maximum_stream_symbols:
        raise ValueError("Pinned observation symbols exceed maximum_stream_symbols")
    pool = tuple(dict.fromkeys(canonical_symbol(symbol, market) for symbol in pool_symbols))
    available = tuple(symbol for symbol in pool if symbol not in reserved)
    ranked: tuple[str, ...] = ()
    strategy_name = ""
    source = "no_ranker"
    if strategy is not None:
        context = PortfolioContext(
            decision_timestamp=decision_timestamp,
            market=market,
            timeframe=timeframe,
            history={symbol: history.get(symbol, ()) for symbol in pool},
            universe=None,
            universe_version="",
            equity=0.0,
            holdings={},
            prices={},
            benchmark_symbol="BTC/USD" if market == Market.CRYPTO else "SPY",
        )
        # Static pool-ranking context: pool membership is caller-supplied, so
        # the permissive None universe is intentional. Explicit opt-in keeps
        # the fail-closed invariant visible and prevents copy-paste into the
        # product decision path.
        require_product_universe(context, allow_static_context=True)
        ranked = tuple(strategy.rank_candidates(context))
        strategy_name = strategy.name
        # Rankers must only ever return pool members; guard defensively.
        ranked = tuple(symbol for symbol in ranked if symbol in available)
        source = "strategy_alpha" if ranked else "no_ranker"
    if len(ranked) < maximum_stream_symbols - len(reserved) and scanner_fallback:
        fill = tuple(symbol for symbol in scanner_fallback if symbol not in reserved and symbol not in ranked)
        ranked = (*ranked, *fill)
        if source == "no_ranker" and fill:
            source = "scanner_quality_fallback"
    capacity = maximum_stream_symbols - len(reserved)
    observation = (*reserved, *ranked[:capacity])
    return ObservationSetResult(
        observation_set=observation,
        ranked_pool=tuple(symbol for symbol in ranked if symbol in pool),
        alpha_scored_count=len(ranked),
        strategy_name=strategy_name,
        source=source,
    )


__all__ = ["ObservationSetResult", "build_observation_set"]
