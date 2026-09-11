"""Independent, pure reconstruction of the frozen R46 V3 target pipeline.

This module is deliberately data-only.  It accepts bars, PIT observation
metadata, and trusted account state and returns immutable target evidence.  It
does not reach into the execution or portfolio implementation, which keeps a
serialized target map from becoming its own verification authority.
"""

from __future__ import annotations

import math
import statistics
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, cast

FROZEN_V3_PARAMETERS: dict[str, Any] = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ("SPY",),
    "rebalance_frequency": "weekly",
    "horizons": (126, 189, 252),
}
FROZEN_ACCOUNT_CAPS: dict[str, float | int] = {
    "daily_loss_limit": 0.005,
    "drawdown_halt": 0.05,
    "equity_sleeve_max": 0.55,
    "combined_gross_max": 0.60,
    "combined_cash_min": 0.40,
    "total_positions_max": 6,
}
_MIN_BETA_RETURNS = 60
_EPS = 1e-12


@dataclass(frozen=True)
class OracleWeight:
    symbol: str
    weight: float
    rank: int


@dataclass(frozen=True)
class OracleTarget:
    decision_timestamp: datetime
    observation_symbols: tuple[str, ...]
    ranked_symbols: tuple[str, ...]
    qualified_symbols: tuple[str, ...]
    scores: tuple[tuple[float, str], ...]
    residual_volatilities: tuple[tuple[str, float], ...]
    weights: tuple[OracleWeight, ...]
    cash_weight: float

    @property
    def weight_map(self) -> dict[str, float]:
        return {item.symbol: item.weight for item in self.weights}

    @property
    def rank_map(self) -> dict[str, int]:
        return {item.symbol: item.rank for item in self.weights}


@dataclass(frozen=True)
class _PendingOrder:
    decision_timestamp: datetime
    symbol: str
    side: str
    requested_qty: float
    client_order_id: str


def _oracle_datetime(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(field) from exc
    raise ValueError(field)


class PendingOrderLedger:
    """Reconstruct non-TLT pending order symbols from trusted target deltas.

    The strict verifier supplies oracle target weights and replay-derived
    pre-trade quantities/marks.  Serialized target maps never enter this
    ledger.  A remainder is retained only when the exact deterministic order
    id has not been fully filled by the next decision boundary.
    """

    def __init__(
        self,
        *,
        fills: Sequence[Mapping[str, object]],
        candidate: bool,
        event_boundaries: Sequence[datetime],
        expiry_by_request: Mapping[tuple[datetime, str, str], datetime] | None = None,
        turnover_band: float = 0.005,
        minimum_order_notional: float = 10.0,
    ) -> None:
        self._fills = tuple(fills)
        self._candidate = bool(candidate)
        self._event_boundaries = frozenset(event_boundaries)
        self._expiry_by_request = {
            (timestamp, str(symbol).upper(), str(side).upper()): expiry
            for (timestamp, symbol, side), expiry in (expiry_by_request or {}).items()
        }
        self._turnover_band = float(turnover_band)
        self._minimum_order_notional = float(minimum_order_notional)
        if self._turnover_band < 0.0 or self._minimum_order_notional < 0.0:
            raise ValueError("oracle_pending_threshold")
        self._pending: tuple[_PendingOrder, ...] = ()

    @staticmethod
    def _fill_quantity(fill: Mapping[str, object]) -> float:
        raw = fill.get("qty")
        if isinstance(raw, bool):
            raise ValueError("oracle_pending_fill_qty")
        try:
            quantity = float(cast(Any, raw))
        except (TypeError, ValueError) as exc:
            raise ValueError("oracle_pending_fill_qty") from exc
        if not math.isfinite(quantity) or quantity <= 0.0:
            raise ValueError("oracle_pending_fill_qty")
        return quantity

    def _filled_quantity(self, order: _PendingOrder, asof: datetime) -> float:
        filled = 0.0
        for raw_fill in self._fills:
            fill_id = str(raw_fill.get("client_order_id", ""))
            if fill_id != order.client_order_id:
                continue
            fill_timestamp = _oracle_datetime(raw_fill.get("timestamp"), "oracle_pending_fill_timestamp")
            if fill_timestamp > asof:
                continue
            fill_decision = _oracle_datetime(
                raw_fill.get("decision_timestamp"), "oracle_pending_fill_decision_timestamp"
            )
            symbol = str(raw_fill.get("symbol", "")).upper()
            side = str(raw_fill.get("side", "")).upper()
            if (
                fill_decision != order.decision_timestamp
                or symbol != order.symbol
                or side != order.side
            ):
                raise ValueError("oracle_pending_fill_identity")
            filled += self._fill_quantity(raw_fill)
        if filled > order.requested_qty + _EPS:
            raise ValueError("oracle_pending_overfill")
        return filled

    def pending_symbols(self, timestamp: datetime) -> tuple[str, ...]:
        """Return open non-TLT symbols immediately before ``timestamp``."""

        remaining: list[_PendingOrder] = []
        for order in self._pending:
            expiry = self._expiry_by_request.get(
                (order.decision_timestamp, order.symbol, order.side)
            )
            if expiry is not None and timestamp > expiry:
                # R46's exact-open TLT leg is discarded after its declared
                # open even if the broker only filled a partial remainder.
                continue
            if self._filled_quantity(order, timestamp) < order.requested_qty - _EPS:
                remaining.append(order)
        self._pending = tuple(remaining)
        return tuple(sorted({order.symbol for order in self._pending}))

    def record_decision(
        self,
        timestamp: datetime,
        *,
        target_weights: Mapping[str, float],
        pretrade_quantities: Mapping[str, float],
        marks: Mapping[str, float],
        equity: float,
        order_id_by_request: Mapping[tuple[str, str], str],
    ) -> None:
        """Record the exact non-TLT orders generated by an ordinary target."""

        if not math.isfinite(equity) or equity <= 0.0:
            raise ValueError("oracle_pending_equity")
        event_boundary = self._candidate and timestamp in self._event_boundaries
        if event_boundary:
            # An event replaces only the exact-open TLT leg.  Ordinary
            # incumbent pending orders remain byte-for-byte open across the
            # marker, matching the engine's event seam.
            self._pending = tuple(order for order in self._pending if order.symbol != "TLT")
        elif self._candidate:
            # Ordinary candidate rebalances clear incumbent orders but retain
            # an exact-open TLT remainder until its declared open/expiry.
            self._pending = tuple(order for order in self._pending if order.symbol == "TLT")
        else:
            self._pending = ()
        normalized_targets = {str(symbol).upper(): float(weight) for symbol, weight in target_weights.items()}
        normalized_quantities = {
            str(symbol).upper(): float(quantity) for symbol, quantity in pretrade_quantities.items()
        }
        normalized_marks = {str(symbol).upper(): float(price) for symbol, price in marks.items()}
        symbols = set(normalized_targets) | set(normalized_quantities)
        if not event_boundary:
            # The overlay engine never creates or cancels a TLT leg at an
            # ordinary incumbent cadence point; an exact-open remainder is
            # carried by the preserved pending tuple above.
            symbols.discard("TLT")
        pending: list[_PendingOrder] = list(self._pending)
        for symbol in sorted(symbols):
            price = normalized_marks.get(symbol, 0.0)
            if not math.isfinite(price) or price <= 0.0:
                raise ValueError("oracle_pending_mark")
            current_quantity = normalized_quantities.get(symbol, 0.0)
            if not math.isfinite(current_quantity) or current_quantity < 0.0:
                raise ValueError("oracle_pending_quantity")
            target_quantity = normalized_targets.get(symbol, 0.0) * equity / price
            delta = target_quantity - current_quantity
            current_weight = current_quantity * price / equity
            target_weight = normalized_targets.get(symbol, 0.0)
            if (
                abs(target_weight - current_weight) < self._turnover_band
                or abs(delta) * price < self._minimum_order_notional
            ):
                continue
            side = "BUY" if delta > 0.0 else "SELL"
            client_order_id = str(order_id_by_request.get((symbol, side), ""))
            if not client_order_id:
                raise ValueError("oracle_pending_order_id")
            pending.append(_PendingOrder(timestamp, symbol, side, abs(delta), client_order_id))
        self._pending = tuple(pending)


def _field(value: object, name: str, default: object = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _symbol(value: object, fallback: object = "") -> str:
    raw = _field(value, "symbol", fallback)
    return str(raw or "").strip().upper()


def _timestamp(value: object) -> datetime:
    raw = _field(value, "timestamp")
    if not isinstance(raw, datetime):
        raise ValueError("oracle_bar_timestamp")
    return raw


def _close(value: object) -> float:
    raw = _field(value, "close")
    if isinstance(raw, bool):
        raise ValueError("oracle_bar_close")
    try:
        result = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("oracle_bar_close") from exc
    return result


def _is_bar(value: object) -> bool:
    return _field(value, "timestamp") is not None and _field(value, "close") is not None


def _bar_groups(bars: object) -> dict[str, tuple[object, ...]]:
    grouped: dict[str, list[object]] = {}

    def append(item: object, fallback: object = "") -> None:
        symbol = _symbol(item, fallback)
        if symbol:
            grouped.setdefault(symbol, []).append(item)

    if isinstance(bars, Mapping):
        if _is_bar(bars):
            append(bars)
        else:
            for key, value in bars.items():
                fallback = key[0] if isinstance(key, tuple) and key else key
                if _is_bar(value):
                    append(value, fallback)
                    continue
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                    for item in value:
                        if _is_bar(item):
                            append(item, fallback)
    elif isinstance(bars, Sequence) and not isinstance(bars, (str, bytes, bytearray)):
        for item in bars:
            if _is_bar(item):
                append(item)
    else:
        raise ValueError("oracle_bars")
    return {
        symbol: tuple(sorted(items, key=lambda item: _timestamp(item)))
        for symbol, items in grouped.items()
    }


def _parameter(parameters: object, name: str, default: object) -> Any:
    if parameters is None:
        return default
    if isinstance(parameters, Mapping):
        return parameters.get(name, default)
    direct = getattr(parameters, name, None)
    if direct is not None:
        return direct
    nested = getattr(parameters, "v3_parameters", None)
    if isinstance(nested, Mapping):
        return nested.get(name, default)
    return default


def _frozen_parameters(parameters: object) -> dict[str, Any]:
    result = dict(FROZEN_V3_PARAMETERS)
    for name, default in FROZEN_V3_PARAMETERS.items():
        value = _parameter(parameters, name, default)
        if name == "exclude_symbols":
            result[name] = tuple(str(item).strip().upper() for item in value)
        elif name == "horizons":
            result[name] = tuple(int(item) for item in value)
        else:
            result[name] = value
    formation = int(result["formation_bars"])
    skip = int(result["skip_bars"])
    beta_window = int(result["beta_window_bars"])
    vol_window = int(result["residual_vol_window"])
    top_n = int(result["top_n"])
    cap = float(result["max_single_weight"])
    quantile = float(result["ivol_reject_quantile"])
    horizons = tuple(int(item) for item in result["horizons"])
    if formation < 60 or skip < 1 or skip >= formation or beta_window < formation + skip:
        raise ValueError("oracle_v3_window_parameters")
    if vol_window < 2 or not 1 <= top_n <= 20 or not 0.0 < cap <= 0.25:
        raise ValueError("oracle_v3_size_parameters")
    if not 0.5 < quantile < 0.95 or not horizons or any(horizon <= skip for horizon in horizons):
        raise ValueError("oracle_v3_filter_parameters")
    result["horizons"] = horizons
    return result


def _compounded(residuals: Sequence[float]) -> float:
    if not residuals:
        return float("-inf")
    value = 1.0
    for residual in residuals:
        value *= 1.0 + float(residual)
        if not math.isfinite(value) or value <= 0.0:
            return float("-inf")
    return value - 1.0


def percentile_ranks(scores: Mapping[str, float]) -> dict[str, float]:
    """Return the frozen tie-averaged percentile rank for every score."""

    if not scores:
        return {}
    ordered = sorted(float(value) for value in scores.values())
    counts: dict[float, int] = {}
    for value in ordered:
        counts[value] = counts.get(value, 0) + 1
    below: dict[float, int] = {}
    running = 0
    for value, count in sorted(counts.items()):
        below[value] = running
        running += count
    size = len(ordered)
    return {
        symbol: (below[float(value)] + 0.5 * counts[float(value)]) / size
        for symbol, value in scores.items()
    }


def multi_horizon_consensus(
    residuals_by_symbol: Mapping[str, Sequence[float]],
    horizons: Sequence[int] = (126, 189, 252),
    skip: int = 21,
) -> dict[str, float]:
    """Compute equal-weight, tie-averaged consensus ranks."""

    if not horizons or skip < 1 or skip >= min(horizons):
        raise ValueError("oracle_horizons")
    ordered_horizons = sorted(set(int(item) for item in horizons))
    if len(residuals_by_symbol) < 2:
        return {}
    per_horizon: list[dict[str, float]] = []
    for horizon in ordered_horizons:
        scores = {
            symbol: _compounded(residuals[-(horizon + skip) : -skip])
            for symbol, residuals in residuals_by_symbol.items()
            if len(residuals) >= horizon + skip
        }
        scores = {symbol: score for symbol, score in scores.items() if math.isfinite(score)}
        if len(scores) == 1:
            continue
        if not scores:
            return {}
        per_horizon.append(percentile_ranks(scores))
    if len(per_horizon) < 2:
        return {}
    common = set(per_horizon[0])
    for ranks in per_horizon[1:]:
        common.intersection_update(ranks)
    if len(common) < 2:
        return {}
    return {
        symbol: sum(ranks[symbol] for ranks in per_horizon) / len(per_horizon)
        for symbol in sorted(common)
    }


def reject_quantile_threshold(values: Sequence[float], quantile: float) -> float:
    """Use the exact index-space quantile used by the incumbent filter."""

    if not values or not 0.0 <= quantile <= 1.0:
        raise ValueError("oracle_quantile")
    ordered = sorted(float(value) for value in values)
    return ordered[int(quantile * (len(ordered) - 1))]


def ols_beta(stock_returns: Sequence[float], market_returns: Sequence[float]) -> float:
    """OLS slope of stock returns on timestamp-aligned market returns."""

    if len(stock_returns) != len(market_returns) or len(stock_returns) < 2:
        return 0.0
    market_mean = statistics.fmean(float(value) for value in market_returns)
    stock_mean = statistics.fmean(float(value) for value in stock_returns)
    variance = statistics.fmean((float(value) - market_mean) ** 2 for value in market_returns) * (len(market_returns) / (len(market_returns) - 1))
    if variance <= 1e-12:
        return 0.0
    covariance = sum(
        (float(stock) - stock_mean) * (float(market) - market_mean)
        for stock, market in zip(stock_returns, market_returns, strict=True)
    ) / (len(stock_returns) - 1)
    return covariance / variance


def aligned_returns(
    stock_bars: Sequence[object],
    market_close_by_timestamp: Mapping[datetime, float],
    window: int = 273,
) -> tuple[list[float], list[float]] | None:
    """Inner-join the trailing stock bars and benchmark bars by timestamp."""

    stock = tuple(stock_bars[-(window + 1) :])
    stock_closes: list[float] = []
    market_closes: list[float] = []
    for bar in stock:
        stock_close = _close(bar)
        market_close = market_close_by_timestamp.get(_timestamp(bar))
        if stock_close > 0.0 and market_close is not None and market_close > 0.0:
            stock_closes.append(stock_close)
            market_closes.append(float(market_close))
    if len(stock_closes) < 3:
        return None
    stock_returns = [stock_closes[index] / stock_closes[index - 1] - 1.0 for index in range(1, len(stock_closes))]
    market_returns = [market_closes[index] / market_closes[index - 1] - 1.0 for index in range(1, len(market_closes))]
    return stock_returns, market_returns


def _volatility(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = statistics.fmean(float(value) for value in values)
    return math.sqrt(
        sum((float(value) - mean) ** 2 for value in values) / (len(values) - 1)
    )


def _terminal_last_bar(value: object) -> datetime | None:
    raw = _field(value, "last_bar")
    return raw if isinstance(raw, datetime) else None


def _snapshots(universe: object) -> tuple[object, ...]:
    if universe is None:
        return ()
    raw: Any = _field(universe, "snapshots")
    if raw is None and isinstance(universe, Sequence) and not isinstance(universe, (str, bytes, bytearray)):
        raw = universe
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    return tuple(sorted(raw, key=lambda item: _field(item, "effective_at", datetime.min)))


def effective_observation_symbols(
    *,
    universe: object = None,
    decision_timestamp: datetime,
    managed_symbols: Sequence[str] = (),
    pending_symbols: Sequence[str] = (),
    terminal_map: Mapping[str, object] | None = None,
    observation_symbols: Sequence[str] | None = None,
    maximum_stream_symbols: int | None = None,
    market: str = "equity",
) -> tuple[str, ...] | None:
    """Mirror the engine's PIT observation/capacity membership calculation."""

    snapshots = _snapshots(universe)
    if not snapshots:
        if observation_symbols is None:
            return None
        return tuple(dict.fromkeys(str(symbol).upper() for symbol in observation_symbols))
    eligible = [item for item in snapshots if _field(item, "effective_at") <= decision_timestamp]
    if not eligible:
        return None
    snapshot = eligible[-1]
    terminal = terminal_map or {}

    def active(symbol: str) -> bool:
        last_bar = _terminal_last_bar(terminal.get(symbol))
        return last_bar is None or last_bar >= decision_timestamp

    raw_observation = _field(snapshot, "observation_set", ()) or ()
    raw_symbols = _field(snapshot, "symbols", ()) or ()
    observation = tuple(str(symbol).upper() for symbol in raw_observation if active(str(symbol).upper()))
    symbols = tuple(str(symbol).upper() for symbol in raw_symbols if active(str(symbol).upper()))
    capacity = int(_field(snapshot, "maximum_stream_symbols", 0) or 0)
    if capacity <= 0:
        capacity = int(maximum_stream_symbols or 0)
    if capacity <= 0:
        return tuple(sorted(dict.fromkeys(observation or symbols)))
    references = tuple(
        symbol
        for symbol in (str(item).upper() for item in (_field(snapshot, "pinned_symbols", ()) or ()))
        if active(symbol)
    )
    managed = tuple(
        dict.fromkeys(
            symbol
            for symbol in (*managed_symbols, *pending_symbols)
            if active(str(symbol).upper())
            for symbol in (str(symbol).upper(),)
        )
    )
    reserved = tuple(dict.fromkeys((*references, *managed)))
    if len(reserved) > capacity:
        raise ValueError("oracle_stream_capacity")
    base = observation or tuple(sorted(symbols))
    tail = tuple(sorted(base)) if market == "equity" and managed else base
    return tuple(dict.fromkeys((*reserved, *tail)))[:capacity]


def _lifetimes_terminal_map(lifetimes: object) -> dict[str, object]:
    if not isinstance(lifetimes, Mapping):
        return {}
    return {str(symbol).upper(): value for symbol, value in lifetimes.items()}


def inverse_volatility_weights(
    selected: Sequence[str],
    residual_volatilities: Mapping[str, float],
    cap: float = 0.10,
) -> dict[str, float]:
    """Return uncoupled inverse-vol weights with the frozen per-name cap.

    The cap is intentionally applied after normalization and is not
    redistributed.  Any remainder therefore remains cash, matching the V3
    target construction and making the operation safe for independent replay.
    """

    if not 0.0 < float(cap) <= 1.0:
        raise ValueError("oracle_inverse_vol_cap")
    raw = {
        str(symbol): 1.0 / float(residual_volatilities[symbol])
        if float(residual_volatilities[symbol]) > 0.0
        else 1.0
        for symbol in selected
    }
    total = sum(raw.values())
    if total <= 0.0:
        return {}
    return {symbol: min(value / total, float(cap)) for symbol, value in raw.items()}


def _raw_weights(selected: Sequence[str], residual_vols: Mapping[str, float], cap: float) -> dict[str, float]:
    return inverse_volatility_weights(selected, residual_vols, cap)


def compute_v3_target(
    *,
    bars: object,
    decision_timestamp: datetime,
    universe: object = None,
    lifetimes: object = None,
    terminal_map: Mapping[str, object] | None = None,
    actions: Sequence[object] = (),
    config: object = None,
    parameters: object = None,
    observation_symbols: Sequence[str] | None = None,
    managed_symbols: Sequence[str] = (),
    pending_symbols: Sequence[str] = (),
    benchmark_symbol: str = "SPY",
    strip_symbols: Sequence[str] = ("TLT",),
    _groups: Mapping[str, Sequence[object]] | None = None,
    _timestamp_groups: Mapping[str, Sequence[datetime]] | None = None,
) -> OracleTarget:
    """Reconstruct one ordinary V3 target from trusted bars and PIT state."""

    del actions, config
    params = _frozen_parameters(parameters)
    groups = {str(symbol).upper(): tuple(series) for symbol, series in (_groups or _bar_groups(bars)).items()}
    benchmark = str(benchmark_symbol).upper()
    effective = effective_observation_symbols(
        universe=universe,
        decision_timestamp=decision_timestamp,
        managed_symbols=managed_symbols,
        pending_symbols=pending_symbols,
        terminal_map=terminal_map or _lifetimes_terminal_map(lifetimes),
        observation_symbols=observation_symbols,
    )
    if effective is None:
        effective = tuple(sorted(groups))
    excluded = {str(item).upper() for item in params["exclude_symbols"]}
    excluded.add(benchmark)
    excluded.update(str(item).upper() for item in strip_symbols)
    formation = int(params["formation_bars"])
    skip = int(params["skip_bars"])
    beta_window = int(params["beta_window_bars"])
    vol_window = int(params["residual_vol_window"])
    if _timestamp_groups is None:
        cutoff_groups: dict[str, tuple[object, ...]] = {
            symbol: tuple(item for item in series if _timestamp(item) <= decision_timestamp)
            for symbol, series in groups.items()
        }
    else:
        cutoff_groups = {
            symbol: tuple(series[: bisect_right(_timestamp_groups[symbol], decision_timestamp)])
            for symbol, series in groups.items()
        }
    market_series = cutoff_groups.get(benchmark, ())
    if len(market_series) < beta_window + 1:
        return OracleTarget(decision_timestamp, effective, (), (), (), (), (), 1.0)
    market_close = {
        _timestamp(item): _close(item)
        for item in market_series
        if _close(item) > 0.0
    }
    minimum_aligned = max(formation + skip, vol_window + 1, _MIN_BETA_RETURNS)
    scored_symbols: list[str] = []
    raw_scores: dict[str, float] = {}
    residual_vols: dict[str, float] = {}
    residuals_by_symbol: dict[str, list[float]] = {}
    for symbol in effective:
        symbol = str(symbol).upper()
        if symbol in excluded:
            continue
        series = cutoff_groups.get(symbol, ())
        if len(series) < beta_window + 1:
            continue
        aligned = aligned_returns(series, market_close, beta_window)
        if aligned is None or len(aligned[0]) < minimum_aligned:
            continue
        stock_returns, market_returns = aligned
        beta = ols_beta(stock_returns, market_returns)
        residuals = [
            stock - beta * market
            for stock, market in zip(stock_returns, market_returns, strict=True)
        ]
        score = _compounded(residuals[-(formation + skip) : -skip])
        residual_vol = _volatility(residuals[-vol_window:])
        if not math.isfinite(score) or not math.isfinite(residual_vol) or score <= 0.0 or residual_vol <= 0.0:
            continue
        scored_symbols.append(symbol)
        raw_scores[symbol] = score
        residual_vols[symbol] = residual_vol * math.sqrt(252.0)
        residuals_by_symbol[symbol] = residuals
    consensus = multi_horizon_consensus(
        residuals_by_symbol,
        tuple(int(item) for item in params["horizons"]),
        skip,
    )
    if not consensus:
        return OracleTarget(decision_timestamp, effective, (), tuple(sorted(scored_symbols)), (), tuple(sorted(residual_vols.items())), (), 1.0)
    qualified = tuple(sorted(scored_symbols))
    scored = [(consensus[symbol], symbol) for symbol in scored_symbols if symbol in consensus]
    threshold = reject_quantile_threshold(
        [residual_vols[symbol] for _score, symbol in scored],
        float(params["ivol_reject_quantile"]),
    ) if scored else float("inf")
    ranked_pairs = sorted(
        ((score, symbol) for score, symbol in scored if residual_vols[symbol] <= threshold),
        key=lambda item: (-item[0], item[1]),
    )
    ranked = tuple(symbol for _score, symbol in ranked_pairs)
    selected = ranked[: int(params["top_n"])]
    weights = _raw_weights(selected, residual_vols, float(params["max_single_weight"]))
    rank_map = {symbol: index for index, symbol in enumerate(selected, start=1)}
    target_weights = tuple(
        OracleWeight(symbol, weights[symbol], rank_map[symbol])
        for symbol in sorted(weights)
        if weights[symbol] > 0.0
    )
    return OracleTarget(
        decision_timestamp=decision_timestamp,
        observation_symbols=effective,
        ranked_symbols=ranked,
        qualified_symbols=qualified,
        scores=tuple(sorted(ranked_pairs, key=lambda item: item[1])),
        residual_volatilities=tuple(sorted(residual_vols.items())),
        weights=target_weights,
        cash_weight=max(0.0, 1.0 - sum(item.weight for item in target_weights)),
    )


class CachedV3TargetOracle:
    """Cache ordinary targets and only retime them at event boundaries."""

    def __init__(self, **defaults: object) -> None:
        self._defaults = dict(defaults)
        supplied_groups = defaults.get("_groups")
        self._groups: Mapping[str, Sequence[object]] | None = cast(
            Mapping[str, Sequence[object]] | None,
            supplied_groups or (_bar_groups(defaults["bars"]) if "bars" in defaults else None),
        )
        supplied_timestamp_groups = defaults.get("_timestamp_groups")
        self._timestamp_groups = supplied_timestamp_groups or (
            {symbol: tuple(_timestamp(item) for item in series) for symbol, series in self._groups.items()}
            if self._groups is not None
            else None
        )
        self._ordinary: dict[datetime, OracleTarget] = {}
        self._shared_target_cache: dict[datetime, OracleTarget] | None = cast(
            dict[datetime, OracleTarget] | None, defaults.get("_target_cache")
        )
        self._cached: OracleTarget | None = None

    @property
    def cached_target(self) -> OracleTarget | None:
        return self._cached

    def target(self, decision_timestamp: datetime, *, event_boundary: bool = False, **overrides: object) -> OracleTarget:
        if event_boundary:
            if self._cached is None:
                raise ValueError("oracle_event_cache_missing")
            return replace(self._cached, decision_timestamp=decision_timestamp)
        if not overrides and decision_timestamp in self._ordinary:
            self._cached = self._ordinary[decision_timestamp]
            return self._cached
        if not overrides and isinstance(self._shared_target_cache, dict) and decision_timestamp in self._shared_target_cache:
            self._cached = self._shared_target_cache[decision_timestamp]
            self._ordinary[decision_timestamp] = self._cached
            return self._cached
        arguments = dict(self._defaults)
        arguments.update(overrides)
        arguments["decision_timestamp"] = decision_timestamp
        arguments.pop("_target_cache", None)
        if "bars" not in overrides and self._groups is not None:
            arguments["_groups"] = self._groups
            arguments["_timestamp_groups"] = self._timestamp_groups
        self._cached = compute_v3_target(**arguments)  # type: ignore[arg-type]
        if not overrides:
            self._ordinary[decision_timestamp] = self._cached
            if isinstance(self._shared_target_cache, dict):
                self._shared_target_cache[decision_timestamp] = self._cached
        return self._cached

    def event_target(self, decision_timestamp: datetime) -> OracleTarget:
        return self.target(decision_timestamp, event_boundary=True)


def add_overlay_weight(target: OracleTarget, symbol: str, weight: float, rank: int = 0) -> OracleTarget:
    """Return a target with one independently declared overlay sleeve."""

    symbol = str(symbol).upper()
    if symbol in target.weight_map:
        raise ValueError("oracle_overlay_duplicate")
    weights = tuple(sorted((*target.weights, OracleWeight(symbol, float(weight), rank)), key=lambda item: item.symbol))
    return replace(target, weights=weights, cash_weight=max(0.0, 1.0 - sum(item.weight for item in weights)))


def apply_account_risk(
    target: OracleTarget,
    *,
    replay_state: Mapping[str, object] | None = None,
    approved_symbols: Sequence[str] | None = None,
    maximum_allocation: float = 0.55,
    caps: Mapping[str, float | int] = FROZEN_ACCOUNT_CAPS,
) -> OracleTarget:
    """Apply the R46 equity sleeve caps to an oracle target.

    R46 has one managed equity account and no external inventory.  This local
    reconstruction therefore follows the production rule ordering exactly:
    per-asset cap, alpha-rank position retention, then managed sleeve scaling.
    """

    approved = {str(symbol).upper() for symbol in approved_symbols} if approved_symbols is not None else set(target.weight_map)
    per_asset = float(FROZEN_V3_PARAMETERS["max_single_weight"])
    desired = {symbol: min(float(weight), per_asset) for symbol, weight in target.weight_map.items()}
    # Symbols outside the approved context are immutable account inventory in
    # the risk overlay.  They reserve positions and consume gross/sleeve
    # capacity, but only remain in the target when the strategy explicitly
    # asks to carry them (the R46 TLT sleeve).
    state = replay_state or {}
    raw_holdings = state.get("holdings", {})
    holdings = raw_holdings if isinstance(raw_holdings, Mapping) else {}
    raw_marks = state.get("marks", {})
    marks = raw_marks if isinstance(raw_marks, Mapping) else {}
    try:
        equity = float(cast(Any, state.get("equity", 1.0)))
    except (TypeError, ValueError):
        equity = 1.0
    if equity <= 0.0:
        raise ValueError("oracle_account_equity")
    current_weights: dict[str, float] = {}
    for raw_symbol, position in holdings.items():
        symbol = str(raw_symbol).upper()
        try:
            quantity = float(_field(position, "qty", 0.0) or 0.0)
            price = float(marks.get(symbol, _field(position, "avg_price", 0.0)) or 0.0)
        except (TypeError, ValueError) as exc:
            raise ValueError("oracle_account_mark") from exc
        if not math.isfinite(quantity) or not math.isfinite(price) or price <= 0.0:
            raise ValueError("oracle_account_mark")
        current_weights[symbol] = abs(quantity) * price / equity
    fixed_symbols = {
        str(symbol).upper()
        for symbol, position in holdings.items()
        if str(symbol).upper() not in approved and float(_field(position, "qty", 0.0) or 0.0) > _EPS
    }
    fixed_gross = 0.0
    for symbol in fixed_symbols:
        position = holdings[symbol]
        price = marks.get(symbol, _field(position, "avg_price", 0.0))
        try:
            fixed_gross += abs(float(_field(position, "qty", 0.0))) * float(price) / equity
        except (TypeError, ValueError):
            raise ValueError("oracle_account_mark") from None
    rank_map = target.rank_map
    daily_pnl_raw = state.get("daily_pnl")
    peak_equity_raw = state.get("peak_equity")
    try:
        daily_pnl = float(cast(Any, daily_pnl_raw)) if daily_pnl_raw is not None else 0.0
        peak_equity = float(cast(Any, peak_equity_raw)) if peak_equity_raw is not None else equity
    except (TypeError, ValueError) as exc:
        raise ValueError("oracle_account_risk_state") from exc
    if not math.isfinite(daily_pnl) or not math.isfinite(peak_equity) or peak_equity <= 0.0:
        raise ValueError("oracle_account_risk_state")
    daily_loss = max(0.0, -daily_pnl / equity)
    drawdown = max(0.0, (peak_equity - equity) / peak_equity)
    daily_loss_limit = float(cast(Any, caps.get("daily_loss_limit", 0.005)))
    drawdown_halt = float(cast(Any, caps.get("drawdown_halt", 0.05)))
    if daily_loss_limit < 0.0 or drawdown_halt < 0.0:
        raise ValueError("oracle_account_risk_caps")
    enforce_drawdown_halt = bool(state.get("enforce_drawdown_halt", False))
    halted = daily_loss >= daily_loss_limit or (enforce_drawdown_halt and drawdown >= drawdown_halt)
    if halted:
        # The production overlay never opens or enlarges managed exposure
        # after a daily-loss breach (and does the same for an enforced
        # drawdown halt); it may only retain the trusted current weight.
        desired = {
            symbol: min(weight, current_weights.get(symbol, 0.0))
            for symbol, weight in desired.items()
        }
    position_cap = int(caps["total_positions_max"])
    mandatory_symbols = set(desired).intersection(fixed_symbols)
    optional_position_limit = max(0, position_cap - len(fixed_symbols))
    priority = sorted(desired, key=lambda symbol: (rank_map.get(symbol, 0) or math.inf, -desired[symbol], symbol))
    optional = [symbol for symbol in priority if symbol not in mandatory_symbols]
    keep = mandatory_symbols | set(optional[:optional_position_limit])
    desired = {symbol: weight for symbol, weight in desired.items() if symbol in keep}
    managed_limit = max(
        0.0,
        min(
            float(maximum_allocation),
            float(caps["equity_sleeve_max"]) - fixed_gross,
            float(caps["combined_gross_max"]) - fixed_gross,
            1.0 - float(caps["combined_cash_min"]) - fixed_gross,
        ),
    )
    total = sum(desired.values())
    if total > managed_limit and total > 0.0:
        scale = managed_limit / total
        desired = {symbol: weight * scale for symbol, weight in desired.items()}
    weights = tuple(
        OracleWeight(symbol, weight, rank_map.get(symbol, 0))
        for symbol, weight in sorted(desired.items())
        if weight > _EPS
    )
    return replace(target, weights=weights, cash_weight=max(0.0, 1.0 - sum(item.weight for item in weights)))


__all__ = [
    "CachedV3TargetOracle",
    "FROZEN_ACCOUNT_CAPS",
    "FROZEN_V3_PARAMETERS",
    "OracleTarget",
    "OracleWeight",
    "PendingOrderLedger",
    "add_overlay_weight",
    "aligned_returns",
    "apply_account_risk",
    "compute_v3_target",
    "effective_observation_symbols",
    "inverse_volatility_weights",
    "multi_horizon_consensus",
    "ols_beta",
    "percentile_ranks",
    "reject_quantile_threshold",
]
