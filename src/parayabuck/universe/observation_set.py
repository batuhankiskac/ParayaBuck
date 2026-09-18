"""Strategy-alpha observation-set builder.

The stream limit is NOT the opportunity universe: the scanner produces the
eligible pool (tradability-first), the strategy's alpha ranks the pool on
batch historical data, and the top ``maximum_stream_symbols`` plus pinned
symbols become the streamed OBSERVATION SET. Managed positions and open
managed orders stay pinned regardless of ranking.

Runtime path: the builder is pure code and has no external artifact dependency.

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
    ordinary_alpha_excluded_symbols: tuple[str, ...] = (),
    scanner_fallback: tuple[str, ...] = (),
) -> ObservationSetResult:
    """Rank the eligible pool with the strategy's alpha and truncate to the
    streamed observation set.

    ``pinned_symbols`` (references, inverse hedges, configured pins) are
    force-kept. ``scanner_fallback`` may fill remaining slots only when its
    history meets the strategy's minimum window and latest-bar alignment;
    young/stale names leave capacity empty instead of wedging every runtime
    decision at the completeness gate.
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
    expected_latest: datetime | None = None
    minimum_history = 0
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
            ordinary_alpha_excluded_symbols=ordinary_alpha_excluded_symbols,
        )
        # Static pool-ranking context: pool membership is caller-supplied, so
        # the permissive None universe is intentional. Explicit opt-in keeps
        # the fail-closed invariant visible and prevents copy-paste into the
        # product decision path.
        require_product_universe(context, allow_static_context=True)
        ranked = tuple(strategy.rank_candidates(context))
        strategy_name = strategy.name
        benchmark = "BTC/USD" if market == Market.CRYPTO else "SPY"
        benchmark_history = history.get(benchmark, ())
        expected_latest = benchmark_history[-1].timestamp if benchmark_history else None
        minimum_history = strategy.minimum_history_bars
        # Rankers must only ever return ready pool members. A stale name may
        # still have enough history to receive an alpha rank, but streaming it
        # would wedge every runtime decision at the complete-batch gate.
        ranked = tuple(
            symbol
            for symbol in ranked
            if symbol in available
            and len(history.get(symbol, ())) >= minimum_history
            and (expected_latest is None or history[symbol][-1].timestamp == expected_latest)
        )
        source = "strategy_alpha" if ranked else "no_ranker"
    if len(ranked) < maximum_stream_symbols - len(reserved) and scanner_fallback:
        fallback_candidates = tuple(
            canonical_symbol(symbol, market)
            for symbol in scanner_fallback
            if canonical_symbol(symbol, market) not in reserved and canonical_symbol(symbol, market) not in ranked
        )
        if strategy is not None:
            fallback_candidates = tuple(
                symbol
                for symbol in fallback_candidates
                if len(history.get(symbol, ())) >= minimum_history
                and (expected_latest is None or history[symbol][-1].timestamp == expected_latest)
            )
        fill = fallback_candidates
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
