from __future__ import annotations

import math
from bisect import bisect_right
from datetime import datetime
from typing import Any

import numpy as np

from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight
from parayabuck.portfolio.momentum import (
    annualize_period_volatility,
    inverse_volatility_weights,
    reject_quantile_threshold,
)

# Residual-momentum literature defaults: 12-month formation / 1-month skip
# (Jegadeesh & Titman 1993) applied to MARKET-RESIDUAL returns (Blitz, Huij &
# Martens 2011). The market component is estimated as a rolling beta against
# the benchmark (SPY), so the score isolates the idiosyncratic return stream
# that momentum is documented to predict.
_DEFAULT_FORMATION_BARS = 252
_DEFAULT_SKIP_BARS = 21
_DEFAULT_BETA_WINDOW_BARS = 273  # formation + skip, beta estimated on all of it
_DEFAULT_RESIDUAL_VOL_WINDOW = 63  # ~3 months, used for the ivol filter and sizing
# Minimum aligned return pairs for a stable beta estimate before a symbol
# qualifies for scoring (beta/residual is the strategy's core math).
_MIN_BETA_RETURNS = 60


class EquityResidualMomentum1dV1(PortfolioStrategy):
    """Long-horizon MARKET-RESIDUAL momentum with an idiosyncratic-risk filter.

    Hypothesis: after stripping the common market component (rolling CAPM-style
    beta against SPY), the residual return stream retains cross-sectional
    momentum predictability without the beta lottery exposure of raw momentum
    (Blitz, Huij & Martens 2011).

    Signal (1d, monthly rebalance):
        1. rolling OLS beta vs SPY over the trailing beta window, computed on
           TIMESTAMP-ALIGNED return pairs (stock and SPY bars are inner-joined
           on their timestamps, so a missing trading day can never misalign
           one series against the other);
        2. residual returns = own return - beta * market return;
        3. residual momentum score = compounded residuals over 12m formation,
           skipping the last month (no same-bar lookahead: the decision fires
           on a completed bar, and the score window ends before the skip);
        4. defensive filter: reject the top idiosyncratic-volatility names
           (lottery-like / extreme-idiosyncratic-risk stocks);
        5. rank by score, inverse-vol sized, per-asset weight cap.

    ``rank_candidates`` reuses the EXACT scoring of ``decide`` so the live
    observation set (stream limit != opportunity universe) is selected by the
    same alpha the strategy trades.

    Research hooks (defaults preserve production math exactly; the 2026-08
    alpha-optimization mission subclasses them):
        ``_transform_cross_sectional_scores`` — once-per-decision re-scoring
            of the eligible pool (blend / multi-horizon consensus);
        ``_select_names`` — selection-boundary modification (hysteresis);
        ``_post_target_weights`` — post-sizing exposure scaling (regime),
            applied BEFORE the engine risk overlay so existing caps stay
            authoritative.
    """

    name = "equity_residual_momentum_v1"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    display_name = "Residual Momentum v1"
    supported_timeframes = ("1d",)
    rebalance_policy_declared = "monthly"
    cadence_label = "monthly"
    # V3 enables additional persisted PAPER diagnostics from exact scoring
    # outputs. This flag is observability-only and never affects targets.
    _include_paper_telemetry_details = False

    @property
    def minimum_history_bars(self) -> int:
        return self.param_int("beta_window_bars", _DEFAULT_BETA_WINDOW_BARS) + 1

    def _validate_parameters(self) -> None:
        formation = self.param_int("formation_bars", _DEFAULT_FORMATION_BARS)
        skip = self.param_int("skip_bars", _DEFAULT_SKIP_BARS)
        beta_window = self.param_int("beta_window_bars", _DEFAULT_BETA_WINDOW_BARS)
        vol_window = self.param_int("residual_vol_window", _DEFAULT_RESIDUAL_VOL_WINDOW)
        top_n = self.param_int("top_n", 8)
        cap = self.param_float("max_single_weight", 0.10)
        ivol_quantile = self.param_float("ivol_reject_quantile", 0.70)
        if formation < 60 or skip < 1:
            raise ValueError("formation_bars and skip_bars are too short")
        if skip >= formation:
            raise ValueError("skip_bars must be smaller than formation_bars")
        if beta_window < formation + skip:
            raise ValueError("beta_window_bars must cover formation plus skip")
        if vol_window < 20:
            raise ValueError("residual_vol_window must be at least 20 bars")
        if not 1 <= top_n <= 20:
            raise ValueError("top_n must be between 1 and 20")
        if not 0 < cap <= 0.25:
            raise ValueError("max_single_weight must be in (0, 0.25]")
        if not 0.5 < ivol_quantile < 0.95:
            raise ValueError("ivol_reject_quantile must be in (0.5, 0.95)")
        excluded = self.parameters.get("exclude_symbols", [])
        if not isinstance(excluded, (list, tuple)) or not all(isinstance(symbol, str) for symbol in excluded):
            raise ValueError("exclude_symbols must be a list of symbols")

    def _score_candidates(self, context: PortfolioContext) -> tuple[tuple[tuple[float, str], ...], dict[str, float]]:
        """Timestamp-aligned residual-momentum ranking of the candidate pool.

        Shared by ``decide`` (portfolio construction) and ``rank_candidates``
        (live observation-set selection) so BACKTEST, PAPER and LIVE score
        identically. Returns (post-ivol-filter ranked pairs, residual vols).
        """
        formation = self.param_int("formation_bars", _DEFAULT_FORMATION_BARS)
        skip = self.param_int("skip_bars", _DEFAULT_SKIP_BARS)
        beta_window = self.param_int("beta_window_bars", _DEFAULT_BETA_WINDOW_BARS)
        vol_window = self.param_int("residual_vol_window", _DEFAULT_RESIDUAL_VOL_WINDOW)
        ivol_quantile = self.param_float("ivol_reject_quantile", 0.70)
        excluded_raw = self.parameters.get("exclude_symbols", [])
        if not isinstance(excluded_raw, (list, tuple)):
            raise ValueError("exclude_symbols must be a list of symbols")
        excluded = {str(symbol).strip().upper() for symbol in (*excluded_raw, context.benchmark_symbol)}
        benchmark_key = canonical_symbol(context.benchmark_symbol, Market.EQUITY)
        # Fast numeric path: if a prepared NumericHistoryView is attached, use
        # vectorized searchsorted slices and keep everything as numpy arrays.
        # This avoids per-symbol list[Bar] → numpy conversion (the 45% bottleneck)
        # and keeps the same math (beta via dot, residuals via vector, compounded
        # via prod) with identical PIT inner-join semantics.
        numeric_view = getattr(context, "numeric_history", None)
        if numeric_view is not None:
            try:
                from parayabuck.backtest.numeric_history import (
                    aligned_returns_numeric,
                    compounded_return_array,
                    ols_beta_arrays,
                    volatility_array,
                )

                bench_series = numeric_view.series(benchmark_key)
                if bench_series is not None:
                    # Only use numeric path if bench has enough history up to decision
                    # (searchsorted check inside aligned will handle missing, but quick check here)
                    return self._score_candidates_numeric(
                        context,
                        numeric_view,
                        bench_series,
                        aligned_returns_numeric,
                        ols_beta_arrays,
                        volatility_array,
                        compounded_return_array,
                        formation,
                        skip,
                        beta_window,
                        vol_window,
                        ivol_quantile,
                        excluded,
                        benchmark_key,
                    )
            except Exception:
                # Fall through to Bar path on any numeric error (parity safe)
                pass
        spy_bars = context.history.get(benchmark_key, ())
        if len(spy_bars) < beta_window + 1:
            return (), {}
        market_close_by_timestamp: dict[datetime, float] = {
            bar.timestamp: bar.close for bar in spy_bars if bar.close > 0
        }

        scored: list[tuple[float, str]] = []
        residual_vols: dict[str, float] = {}
        residuals_by_symbol: dict[str, list[float]] = {}
        minimum_aligned = max(formation + skip, vol_window + 1, _MIN_BETA_RETURNS)
        # Precompute universe membership set once per decision (avoid per-symbol bisect).
        universe_set: frozenset[str] | None = None
        try:
            snapshots = getattr(context.universe, "snapshots", None)
            if snapshots:
                idx = bisect_right(snapshots, context.decision_timestamp, key=lambda x: x.effective_at)
                if idx > 0:
                    universe_set = snapshots[idx - 1].symbols
                else:
                    universe_set = frozenset()
        except Exception:
            universe_set = None
        history = context.history
        min_history_bars = self.minimum_history_bars
        in_universe = context.in_universe
        # Iterate over universe symbols directly when possible (155 avg vs 1200 history keys) to reduce per-decision overhead 7x.
        if universe_set is not None:
            for symbol in sorted(universe_set):
                if symbol in excluded:
                    continue
                bars = history.get(symbol)
                if bars is None or len(bars) < min_history_bars:
                    continue
                aligned = _aligned_returns(bars, market_close_by_timestamp, beta_window)
                if aligned is None or len(aligned[0]) < minimum_aligned:
                    continue
                stock_returns, market_returns = aligned
                beta = _ols_beta(stock_returns, market_returns)
                residuals = [stock - beta * market for stock, market in zip(stock_returns, market_returns, strict=True)]
                score = self._candidate_score(residuals, formation=formation, skip=skip)
                residual_vol = _volatility(residuals[-vol_window:])
                if not math.isfinite(score) or not math.isfinite(residual_vol):
                    continue
                if score <= 0 or residual_vol <= 0:
                    continue
                scored.append((score, symbol))
                residual_vols[symbol] = annualize_period_volatility(residual_vol, 252)
                residuals_by_symbol[symbol] = residuals
        else:
            sorted_symbols = sorted(history)
            for symbol in sorted_symbols:
                if symbol in excluded:
                    continue
                if not in_universe(symbol):
                    continue
                bars = history[symbol]
                if len(bars) < min_history_bars:
                    continue
                aligned = _aligned_returns(bars, market_close_by_timestamp, beta_window)
                if aligned is None or len(aligned[0]) < minimum_aligned:
                    continue
                stock_returns, market_returns = aligned
                beta = _ols_beta(stock_returns, market_returns)
                residuals = [stock - beta * market for stock, market in zip(stock_returns, market_returns, strict=True)]
                score = self._candidate_score(residuals, formation=formation, skip=skip)
                residual_vol = _volatility(residuals[-vol_window:])
                if not math.isfinite(score) or not math.isfinite(residual_vol):
                    continue
                if score <= 0 or residual_vol <= 0:
                    continue
                scored.append((score, symbol))
                residual_vols[symbol] = annualize_period_volatility(residual_vol, 252)
                residuals_by_symbol[symbol] = residuals
        # pipeline. Default: per-symbol scores pass through unchanged.
        scored = self._transform_cross_sectional_scores(
            scored, residuals_by_symbol, residual_vols, formation=formation, skip=skip
        )
        # Defensive filter: reject the extreme idiosyncratic-volatility names
        # (lottery-like tail) before ranking, not as a second alpha signal.
        # ``reject_quantile_threshold`` keeps ~q of the qualified pool (ties
        # kept); the previous ``int(q * n)`` index over-retained one name
        # (q*n + 1 of n), materially so for small pools.
        scored = self._apply_ivol_filter(scored, residual_vols, ivol_quantile)
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(scored), residual_vols

    def _score_candidates_numeric(
        self,
        context: PortfolioContext,
        numeric_view: Any,
        bench_series: Any,
        aligned_fn: Any,
        beta_fn: Any,
        vol_fn: Any,
        compounded_fn: Any,
        formation: int,
        skip: int,
        beta_window: int,
        vol_window: int,
        ivol_quantile: float,
        excluded: set[str],
        benchmark_key: str,
    ) -> tuple[tuple[tuple[float, str], ...], dict[str, float]]:
        """Numeric fast path for scoring using ``NumericHistoryView``.

        Keeps the same PIT inner-join and math but operates on immutable
        ``np.ndarray`` views (searchsorted slices) without per-symbol
        ``list[Bar]`` allocation or ``.tolist()`` round trips. Falls back to
        the Bar path on any error to preserve parity.
        """
        # Local aliases for speed and to avoid attribute lookup per symbol
        minimum_aligned = max(formation + skip, vol_window + 1, _MIN_BETA_RETURNS)
        min_history_bars = self.minimum_history_bars
        # Universe set once per decision
        universe_set: frozenset[str] | None = None
        try:
            snapshots = getattr(context.universe, "snapshots", None)
            if snapshots:
                idx = bisect_right(snapshots, context.decision_timestamp, key=lambda x: x.effective_at)
                if idx > 0:
                    universe_set = snapshots[idx - 1].symbols
                else:
                    universe_set = frozenset()
        except Exception:
            universe_set = None
        # Bench length check via searchsorted quickly: need at least beta_window+1 closes up to decision
        try:
            import numpy as np

            bench_ts = bench_series.timestamps
            bench_dt = np.datetime64(context.decision_timestamp.replace(tzinfo=None))
            bench_end = int(np.searchsorted(bench_ts, bench_dt, side="right"))
            if bench_end < beta_window + 1:
                return (), {}
        except Exception:
            return (), {}
        scored: list[tuple[float, str]] = []
        residual_vols: dict[str, float] = {}
        # residuals_by_symbol may hold ndarrays for V3 consensus
        residuals_by_symbol: dict[str, Any] = {}
        if universe_set is not None:
            # Numeric view fallback: history symbols may include via view.symbols()
            # but we iterate universe directly
            for symbol in sorted(universe_set):
                if symbol in excluded:
                    continue
                stock_series = numeric_view.series(symbol)
                if stock_series is None:
                    continue
                # Quick history length check via searchsorted (PIT)
                try:
                    import numpy as np

                    end = int(
                        np.searchsorted(
                            stock_series.timestamps,
                            np.datetime64(context.decision_timestamp.replace(tzinfo=None)),
                            side="right",
                        )
                    )
                    if end < min_history_bars:
                        continue
                except Exception:
                    continue
                aligned = aligned_fn(stock_series, bench_series, context.decision_timestamp, beta_window)
                if aligned is None:
                    continue
                stock_returns, market_returns = aligned
                if len(stock_returns) < minimum_aligned:
                    continue
                beta = beta_fn(stock_returns, market_returns)
                # residuals as ndarray (no python loop)
                residuals = stock_returns - beta * market_returns
                # formation/skip slice via numpy
                if len(residuals) < formation + skip:
                    continue
                # Use array slicing; compounded expects ndarray
                score_slice = residuals[-(formation + skip) : -skip if skip != 0 else None]
                score = compounded_fn(score_slice)
                vol_slice = residuals[-vol_window:]
                residual_vol = vol_fn(vol_slice)
                if not math.isfinite(score) or not math.isfinite(residual_vol):
                    continue
                if score <= 0 or residual_vol <= 0:
                    continue
                scored.append((float(score), symbol))
                residual_vols[symbol] = annualize_period_volatility(float(residual_vol), 252)
                residuals_by_symbol[symbol] = residuals
        else:
            # Static/permissive universe: iterate view symbols and check in_universe
            try:
                symbols = sorted(numeric_view.series_by_symbol.keys())
            except AttributeError:
                symbols = sorted(numeric_view.symbols())
            in_universe = context.in_universe
            for symbol in symbols:
                if symbol in excluded:
                    continue
                if not in_universe(symbol):
                    continue
                stock_series = numeric_view.series(symbol)
                if stock_series is None:
                    continue
                try:
                    import numpy as np

                    end = int(
                        np.searchsorted(
                            stock_series.timestamps,
                            np.datetime64(context.decision_timestamp.replace(tzinfo=None)),
                            side="right",
                        )
                    )
                    if end < min_history_bars:
                        continue
                except Exception:
                    continue
                aligned = aligned_fn(stock_series, bench_series, context.decision_timestamp, beta_window)
                if aligned is None:
                    continue
                stock_returns, market_returns = aligned
                if len(stock_returns) < minimum_aligned:
                    continue
                beta = beta_fn(stock_returns, market_returns)
                residuals = stock_returns - beta * market_returns
                if len(residuals) < formation + skip:
                    continue
                score_slice = residuals[-(formation + skip) : -skip if skip != 0 else None]
                score = compounded_fn(score_slice)
                vol_slice = residuals[-vol_window:]
                residual_vol = vol_fn(vol_slice)
                if not math.isfinite(score) or not math.isfinite(residual_vol):
                    continue
                if score <= 0 or residual_vol <= 0:
                    continue
                scored.append((float(score), symbol))
                residual_vols[symbol] = annualize_period_volatility(float(residual_vol), 252)
                residuals_by_symbol[symbol] = residuals
        scored = self._transform_cross_sectional_scores(
            scored, residuals_by_symbol, residual_vols, formation=formation, skip=skip
        )
        scored = self._apply_ivol_filter(scored, residual_vols, ivol_quantile)
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(scored), residual_vols

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        ranked, _vols = self._score_candidates(context)
        return tuple(symbol for _score, symbol in ranked)

    def _candidate_score(self, residuals: list[float], *, formation: int, skip: int) -> float:
        """Ranking score over the formation window (skip excluded).

        Default: compounded residual return. Research variants may
        standardize (score / residual stdev) by overriding ONLY this hook so
        every variant shares the exact beta/residual/filter pipeline.
        """
        return _compounded_return(residuals[-formation - skip : -skip])

    def _apply_ivol_filter(
        self,
        scored: list[tuple[float, str]],
        residual_vols: dict[str, float],
        quantile: float,
    ) -> list[tuple[float, str]]:
        """Defensive idiosyncratic-vol rejection (see module docstring).

        Separated as a hook so ablations can disable it explicitly instead
        of abusing parameter values outside their validated ranges.
        """
        if not scored:
            return scored
        threshold = reject_quantile_threshold([residual_vols[symbol] for _score, symbol in scored], quantile)
        return [(score, symbol) for score, symbol in scored if residual_vols[symbol] <= threshold]

    def _transform_cross_sectional_scores(
        self,
        scored: list[tuple[float, str]],
        residuals_by_symbol: dict[str, list[float]],
        residual_vols: dict[str, float],
        *,
        formation: int,
        skip: int,
    ) -> list[tuple[float, str]]:
        """Cross-sectional research scoring hook (see ``_score_candidates``).

        Runs once per decision over the eligible pool with every symbol's
        residual series and 63-bar annualized residual vols available, so
        variants may compute BLENDED or MULTI-HORIZON cross-sectional scores
        deterministically without re-running the beta/residual pipeline.
        Default: the underlying per-symbol scores pass through unchanged.
        """
        return scored

    def _select_names(
        self,
        ranked: tuple[tuple[float, str], ...],
        residual_vols: dict[str, float],
        context: PortfolioContext,
        top_n: int,
    ) -> tuple[str, ...]:
        """Position-selection hook (see ``decide``) — ranking unchanged.

        Default: the best ``top_n`` scores. Research may override to encode
        hysteresis around the selection boundary without touching math: the
        overlay's per-position/rank caps remain in force on the result.
        """
        return tuple(symbol for _score, symbol in ranked[:top_n])

    def _post_target_weights(
        self,
        weights: dict[str, float],
        context: PortfolioContext,
    ) -> dict[str, float]:
        """Post-sizing hook running AFTER alpha target weights are set and
        BEFORE the engine risk overlay sees them.

        Default: pass-through. Research variants may scale the allocation
        (e.g. market-regime exposure derating) here; existing per-position
        and gross-exposure caps are then applied on the scaled targets.
        """
        return weights

    def _position_weights(self, selected: tuple[str, ...], residual_vols: dict[str, float]) -> dict[str, float]:
        """Sizing for the selected names (inverse-vol, capped). Hook."""
        return dict(
            inverse_volatility_weights(
                selected, residual_vols, maximum_weight=self.param_float("max_single_weight", 0.10)
            )
        )

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        top_n = self.param_int("top_n", 8)
        beta_window = self.param_int("beta_window_bars", _DEFAULT_BETA_WINDOW_BARS)

        benchmark_key = canonical_symbol(context.benchmark_symbol, Market.EQUITY)
        if len(context.history.get(benchmark_key, ())) < beta_window + 1:
            return self._cash_portfolio(context, "market_factor_unavailable")

        ranked, residual_vols = self._score_candidates(context)
        if not ranked:
            return self._cash_portfolio(context, "no_qualified_candidates")
        selected = self._select_names(ranked, residual_vols, context, top_n)
        weights = dict(self._post_target_weights(self._position_weights(selected, residual_vols), context))
        # Alpha rank travels with the target (1 = best): the risk overlay's
        # position-count enforcement retains the strategy's best-ranked
        # names instead of inventing a second ranking from target weights.
        rank_by_symbol = {symbol: index for index, symbol in enumerate(selected, start=1)}
        targets = tuple(
            TargetWeight(
                symbol,
                weight,
                reason="residual_momentum",
                rank=rank_by_symbol[symbol],
            )
            for symbol, weight in sorted(weights.items())
            if weight > 0
        )
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=max(0.0, 1.0 - sum(weight.weight for weight in targets)),
            rebalance_reason=(RebalanceReason.SCHEDULED if targets else RebalanceReason.RISK_OFF),
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={
                "qualified_symbols": len(ranked),
                "ranked_symbols": len(ranked),
                "selected_symbols": selected,
                "ivol_filter_quantile": self.param_float("ivol_reject_quantile", 0.70),
                "hypothesis": (
                    "12m/1m market-residual momentum (rolling beta vs SPY, "
                    "timestamp-aligned returns), top idiosyncratic-volatility "
                    f"names rejected, {self.cadence_label}, inverse-vol sized"
                ),
                "explanations": _equity_explanations(
                    context,
                    ranked,
                    residual_vols,
                    weights,
                    include_paper_telemetry=self._include_paper_telemetry_details,
                ),
            },
        )

    def _cash_portfolio(self, context: PortfolioContext, detail: str) -> TargetPortfolio:
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=(),
            cash_weight=1.0,
            rebalance_reason=RebalanceReason.RISK_OFF,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={"risk_off_detail": detail},
        )


def _equity_explanations(
    context: PortfolioContext,
    ranked: tuple[tuple[float, str], ...],
    residual_vols: dict[str, float],
    weights: dict[str, float],
    *,
    include_paper_telemetry: bool = False,
) -> dict[str, dict[str, float | int]]:
    """Concise per-holding decision reasons for the selected names.

    Ranking numbers are read straight from ``_score_candidates`` output
    (alpha rank is the 1-based position in the post-filter ranked pool; the
    momentum score and annualized idio-vol are the exact production values).
    ``current_weight`` is the holding recorded in ``context.holdings``
    (position quantity from the account snapshot; 0.0 when flat).
    """
    pool_vols = sorted(residual_vols[symbol] for _score, symbol in ranked)
    pool_size = max(1, len(pool_vols))
    explanations: dict[str, dict[str, float | int]] = {}
    for rank, (score, symbol) in enumerate(ranked, start=1):
        if symbol not in weights:
            continue
        # pool_vols is sorted, so the count of values <= the symbol's vol is
        # its bisect index (identical to the linear scan it replaces).
        below_or_equal = bisect_right(pool_vols, residual_vols[symbol])
        explanation: dict[str, float | int] = {
            "alpha_rank": rank,
            "residual_momentum_score": score,
            "idio_vol_percentile": round(below_or_equal / pool_size, 4),
            "target_weight": weights[symbol],
            "current_weight": float(context.holdings.get(symbol, 0.0)),
        }
        if include_paper_telemetry:
            # For V3 this is the final equal-mean consensus score. Preserve
            # the existing score field for compatibility and add explicit
            # operator-facing PAPER names.
            explanation["consensus_score"] = score
            explanation["residual_volatility"] = residual_vols[symbol]
        explanations[symbol] = explanation
    return explanations


def _aligned_returns(
    stock_bars: tuple[Bar, ...],
    market_close_by_timestamp: dict[datetime, float],
    window: int,
) -> tuple[list[float], list[float]] | None:
    """Timestamp-INNER-JOINed simple returns over the stock's trailing window.

    The stock's last ``window + 1`` bars are joined against the market series
    ON TIMESTAMP (never positionally): a stock that misses a trading day must
    not pair its Thursday return with the market's Wednesday return. Returns
    (stock_returns, market_returns) over the aligned pairs, or None when too
    few aligned pairs exist for a stable beta estimate. ``market_close_by_timestamp``
    is the market's close lookup, built once per decision.
    """
    stock = stock_bars[-(window + 1) :]
    get = market_close_by_timestamp.get
    stock_closes: list[float] = []
    market_closes: list[float] = []
    for bar in stock:
        c = bar.close
        if c <= 0:
            continue
        mc = get(bar.timestamp)
        if mc is not None and mc > 0:
            stock_closes.append(c)
            market_closes.append(mc)
    if len(stock_closes) < 3:
        return None
    n = len(stock_closes)
    if n < 32:
        stock_returns: list[float] = []
        market_returns: list[float] = []
        for i in range(1, n):
            prev_s = stock_closes[i - 1]
            prev_m = market_closes[i - 1]
            if prev_s > 0 and prev_m > 0:
                stock_returns.append(stock_closes[i] / prev_s - 1)
                market_returns.append(market_closes[i] / prev_m - 1)
        if len(stock_returns) < 2:
            return None
        return stock_returns, market_returns
    s_arr = np.array(stock_closes, dtype=np.float64)
    m_arr = np.array(market_closes, dtype=np.float64)
    stock_returns_arr = s_arr[1:] / s_arr[:-1] - 1.0
    market_returns_arr = m_arr[1:] / m_arr[:-1] - 1.0
    if len(stock_returns_arr) < 2:
        return None
    return stock_returns_arr.tolist(), market_returns_arr.tolist()


def _ols_beta(stock_returns: list[float], market_returns: list[float]) -> float:
    """OLS slope of stock returns on market returns (aligned pairs)."""
    n = len(stock_returns)
    if n < 2:
        return 0.0
    market_mean = sum(market_returns) / n
    stock_mean = sum(stock_returns) / n
    if n >= 64:
        m_arr = np.array(market_returns, dtype=np.float64)
        s_arr = np.array(stock_returns, dtype=np.float64)
        dm = m_arr - market_mean
        ds = s_arr - stock_mean
        var = float(np.dot(dm, dm) / (n - 1))
        if var <= 1e-12:
            return 0.0
        cov = float(np.dot(ds, dm) / (n - 1))
        return cov / var
    var_sum = 0.0
    for v in market_returns:
        d = v - market_mean
        var_sum += d * d
    variance = var_sum / (n - 1)
    if variance <= 1e-12:
        return 0.0
    cov_sum = 0.0
    for s, m in zip(stock_returns, market_returns, strict=True):
        cov_sum += (s - stock_mean) * (m - market_mean)
    covariance = cov_sum / (n - 1)
    return covariance / variance


def _compounded_return(residuals: list[float]) -> float:
    if not residuals:
        return float("-inf")
    value = 1.0
    for residual in residuals:
        value *= 1.0 + residual
        if not math.isfinite(value) or value <= 0:
            return float("-inf")
    return value - 1.0


def _volatility(residuals: list[float]) -> float:
    if len(residuals) < 2:
        return 0.0
    n = len(residuals)
    mean = sum(residuals) / n
    if n >= 64:
        arr = np.array(residuals, dtype=np.float64)
        diff = arr - mean
        return math.sqrt(float(np.dot(diff, diff) / (n - 1)))
    var_sum = 0.0
    for v in residuals:
        d = v - mean
        var_sum += d * d
    return math.sqrt(var_sum / (n - 1))


class EquityResidualMomentum1dV2(EquityResidualMomentum1dV1):
    """Weekly-cadence identity of the validated residual-momentum alpha.

    RESEARCH-validated cadence variant (2026-08-18 cadence mission): identical
    signal math and parameters to ``equity_residual_momentum_v1``; only the
    rebalance cadence differs (weekly vs monthly). The cadence rides in the
    strategy parameters (``rebalance_frequency``) so the configuration digest,
    persisted decisions and dashboard identity are distinct from v1 — v1 stays
    the frozen production selection until an operator promotes v2.
    """

    name = "equity_residual_momentum_v2"
    version = "2.0.0"
    display_name = "Residual Momentum v2 (weekly)"
    rebalance_policy_declared = "weekly"
    cadence_label = "weekly"

    def _validate_parameters(self) -> None:
        self.parameters.setdefault("rebalance_frequency", "weekly")
        super()._validate_parameters()


__all__ = ["EquityResidualMomentum1dV1", "EquityResidualMomentum1dV2"]
