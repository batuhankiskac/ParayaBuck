from __future__ import annotations

import csv
import itertools
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from parayabuck.backtest.portfolio_engine import (
    OrderSide,
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
    TerminalLifecycle,
)
from parayabuck.backtest.portfolio_report import PortfolioReport, build_portfolio_report
from parayabuck.backtest.universe import (
    PointInTimeUniverse,
    build_static_equity_point_in_time_universe,
    hash_point_in_time_universe,
)
from parayabuck.config import Config
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType
from parayabuck.execution.portfolio import (
    portfolio_execution_policy_from_config,
    portfolio_risk_limits_from_config,
)
from parayabuck.logger import get_logger
from parayabuck.markets.nyse_calendar import nyse_sessions, session_bounds
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.markets.timeframes import NEW_YORK, TIMEFRAME_MINUTES, is_regular_equity_time
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for

_MAX_OPTIMIZATION_COMBINATIONS = 256
_CSV_TIMEFRAME_ALIASES = {"1d": "1d", "1Day": "1d"}
_LEGACY_ACTION_COLUMNS = {"timestamp", "symbol", "action_type", "value"}
_CANONICAL_ACTION_COLUMNS = {
    "action_type",
    "symbol",
    "ex_date",
    "record_date",
    "payable_date",
    "rate",
    "old_rate",
    "new_rate",
    "source_symbol",
    "target_symbol",
    "raw_id",
}
_CANONICAL_NON_CASH_ACTION_TYPES = {"stock_dividends", "forward_splits", "reverse_splits"}
_CANONICAL_SPLIT_ACTION_TYPES = {"forward_splits", "reverse_splits"}
_STOCK_DIVIDEND_ECHO_TOLERANCE = Decimal("1e-5")

log = get_logger("parayabuck.backtest.product")


@dataclass(frozen=True)
class ProductBacktestResult:
    run_id: str
    result: PortfolioBacktestResult
    report: PortfolioReport
    universe: PointInTimeUniverse
    output_dir: str
    score: float


def _require_equity(market: Market) -> None:
    if market != Market.EQUITY:
        raise ValueError(f"Product backtests support equity only; got market {market.value!r}")


def _canonical_csv_timeframe(raw_timeframe: object) -> str:
    timeframe = str(raw_timeframe)
    if timeframe in _CSV_TIMEFRAME_ALIASES:
        return _CSV_TIMEFRAME_ALIASES[timeframe]
    if timeframe in TIMEFRAME_MINUTES:
        return timeframe
    raise ValueError(f"Unsupported CSV timeframe: {timeframe}")


def load_csv_bars(path: str | Path, *, default_market: Market, strict: bool = True) -> list[Bar]:
    """Load priced equity bars; ``strict`` fails closed on a NYSE calendar error.

    ``strict=False`` restores the degraded lookup (weekday fallback when the
    calendar is unavailable) for offline exploration only.
    """
    source = Path(path)
    parsed = _read_priced_csv_rows(source, default_market=default_market)
    # One batched NYSE range lookup per load: a per-row session_bounds call
    # resolves its own uncached single-date schedule (~50-100 ms), so a few
    # thousand daily rows cost tens of seconds. The batched set carries
    # exactly the same session dates as the per-row lookups.
    session_dates: set[object] = set()
    batched = False
    daily_dates = sorted(
        {
            stamp.astimezone(NEW_YORK).date()
            for _, stamp, raw, _ in parsed
            if str(raw.get("timeframe") or "1d") in ("1d", "1Day")
        }
    )
    if daily_dates:
        try:
            session_dates = {day for day, _, _ in nyse_sessions(min(daily_dates), max(daily_dates))}
            batched = True
        except Exception as exc:
            if strict:
                # A product run must not silently keep holidays as sessions
                # through a weekday-only fallback.
                raise ValueError(
                    "NYSE calendar unavailable; cannot validate daily bar sessions "
                    f"(pass strict=False to use the weekday fallback): {exc}"
                ) from exc
            # Non-strict: fall through to the degraded per-date lookup, which
            # carries its own fallback.
    rows: list[Bar] = []
    for row_number, timestamp, raw, market in parsed:
        timeframe = _canonical_csv_timeframe(raw.get("timeframe") or "1d")
        raw_volume = str(raw["volume"] or "").strip()
        if not raw_volume:
            raise ValueError(f"CSV row {row_number} has no volume for {raw['symbol']}")
        if timestamp.weekday() >= 5:
            raise ValueError(f"Equity CSV contains a weekend bar: {timestamp.isoformat()}")
        if timeframe != "1d" and not is_regular_equity_time(timestamp):
            raise ValueError(f"Equity CSV contains an out-of-session bar: {timestamp.isoformat()}")
        if timeframe == "1d":
            if batched:
                in_session = timestamp.astimezone(NEW_YORK).date() in session_dates
            else:
                in_session = session_bounds(timestamp.astimezone(NEW_YORK).date()) is not None
            if not in_session:
                # Live never sees a bar on a non-session (holiday) date, so a
                # backtest must not decide on one either: drop it, loudly.
                log.warning("Equity CSV contains a non-session bar (holiday); dropped: %s", timestamp.isoformat())
                continue
        rows.append(
            Bar(
                timestamp=timestamp,
                symbol=str(raw["symbol"]),
                timeframe=timeframe,
                open=float(raw["open"]),
                high=float(raw["high"]),
                low=float(raw["low"]),
                close=float(raw["close"]),
                volume=float(raw_volume),
                market=market,
            )
        )
    return rows


def _read_priced_csv_rows(
    source: Path, *, default_market: Market
) -> list[tuple[int, datetime, dict[str, str], Market]]:
    priced: list[tuple[int, datetime, dict[str, str], Market]] = []
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        # ``volume`` is required: the fill model sizes every order from the
        # trailing average volume, so a missing column (formerly read as 0.0)
        # deferred every order and produced a silent zero-trade backtest.
        required = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
        if not required.issubset(reader.fieldnames or ()):
            missing = sorted(required - set(reader.fieldnames or ()))
            raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")
        for row_number, raw in enumerate(reader, start=2):
            timestamp = datetime.fromisoformat(str(raw["timestamp"]))
            market = Market(str(raw.get("market") or default_market.value).lower())
            _require_equity(market)
            timeframe = _canonical_csv_timeframe(raw.get("timeframe") or "1d")
            if timestamp.tzinfo is None:
                # A naive daily stamp names the New York session date. Read as
                # 00:00 UTC it would be the PREVIOUS New York date (19:00/20:00
                # ET), shifting every session lookup by one day. Intraday naive
                # stamps keep the historical UTC reading.
                timestamp = timestamp.replace(tzinfo=NEW_YORK if timeframe == "1d" else UTC)
            priced.append((row_number, timestamp, raw, market))
    return priced


def _action_value(raw_value: object, *, row_number: int) -> Decimal:
    try:
        value = Decimal(str(raw_value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Corporate-action row {row_number} has an invalid value") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"Corporate-action row {row_number} value must be finite and positive")
    return value


def _legacy_action_timestamp(raw_timestamp: object, *, row_number: int) -> datetime:
    try:
        timestamp = datetime.fromisoformat(str(raw_timestamp))
    except ValueError as exc:
        raise ValueError(f"Corporate-action row {row_number} has an invalid timestamp") from exc
    return timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)


def _canonical_action_timestamp(raw_ex_date: object, *, row_number: int) -> datetime:
    value = str(raw_ex_date)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Corporate-action row {row_number} has an invalid ex_date") from exc
    return parsed.replace(tzinfo=UTC)


def _split_ratio(raw: dict[str, str], *, row_number: int) -> Decimal:
    old_rate = _action_value(raw["old_rate"], row_number=row_number)
    new_rate = _action_value(raw["new_rate"], row_number=row_number)
    return new_rate / old_rate


def load_csv_corporate_actions(path: str | Path) -> list[CorporateAction]:
    """Load explicit dividend cash flows for split-adjusted equity price data.

    The product backtest runs on split-adjusted bars, so a dividend must be
    credited per ADJUSTED share. Canonical v5 ``cash_dividends.rate`` is the
    raw announced per-share amount (e.g. AAPL 2020-05-08 rate 0.82 alongside
    a split-adjusted close of 77.53, pre-4:1-split raw ~310; QLD 2016-03-23
    rate 0.084812 alongside close 4.448 after four later 2:1 splits), so
    each rate is divided by the cumulative ratio of the same symbol's
    forward/reverse splits with a strictly later ex_date. This assumes the
    action file and the bars are one snapshot (no split in the file that the
    bar download predates). Stock dividends are not applied, and the
    cash_dividends row v5 repeats for each of them (rate = share ratio) is
    dropped. The legacy dividend-only schema carries no split or
    stock-dividend rows and is passed through unadjusted.
    """
    actions: list[CorporateAction] = []
    seen: set[tuple[str, datetime, CorporateActionType, Decimal]] = set()
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or ())
        if _LEGACY_ACTION_COLUMNS.issubset(fieldnames):
            schema = "legacy"
        elif _CANONICAL_ACTION_COLUMNS.issubset(fieldnames):
            schema = "canonical_v5"
        elif {"ex_date", "rate"} & fieldnames:
            missing = sorted(_CANONICAL_ACTION_COLUMNS - fieldnames)
            raise ValueError(f"Canonical corporate-action CSV is missing required columns: {', '.join(missing)}")
        else:
            missing = sorted(_LEGACY_ACTION_COLUMNS - fieldnames)
            raise ValueError(f"Corporate-action CSV is missing required columns: {', '.join(missing)}")
        rows = list(enumerate(reader, start=2))

    # A set: a split repeated under another raw_id is one economic event and
    # must not compound into the dividend rescale.
    splits: dict[str, set[tuple[datetime, Decimal]]] = {}
    stock_dividend_ratios: dict[tuple[str, datetime], list[Decimal]] = {}
    if schema == "canonical_v5":
        for row_number, raw in rows:
            raw_action_type = str(raw["action_type"]).lower()
            if raw_action_type in _CANONICAL_SPLIT_ACTION_TYPES:
                splits.setdefault(canonical_symbol(str(raw["symbol"]), Market.EQUITY), set()).add(
                    (
                        _canonical_action_timestamp(raw["ex_date"], row_number=row_number),
                        _split_ratio(raw, row_number=row_number),
                    )
                )
            elif raw_action_type == "stock_dividends":
                stock_dividend_ratios.setdefault(
                    (
                        canonical_symbol(str(raw["symbol"]), Market.EQUITY),
                        _canonical_action_timestamp(raw["ex_date"], row_number=row_number),
                    ),
                    [],
                ).append(_action_value(raw["rate"], row_number=row_number))

    for row_number, raw in rows:
        raw_action_type = str(raw["action_type"]).lower()
        if schema == "canonical_v5" and raw_action_type in _CANONICAL_NON_CASH_ACTION_TYPES:
            continue
        expected_action_type = "dividend" if schema == "legacy" else "cash_dividends"
        if raw_action_type != expected_action_type:
            raise ValueError(f"Unsupported corporate-action type on row {row_number}: {raw_action_type}")
        timestamp = (
            _legacy_action_timestamp(raw["timestamp"], row_number=row_number)
            if schema == "legacy"
            else _canonical_action_timestamp(raw["ex_date"], row_number=row_number)
        )
        value = _action_value(raw["value"] if schema == "legacy" else raw["rate"], row_number=row_number)
        raw_symbol = str(raw["symbol"])
        symbol = canonical_symbol(raw_symbol, Market.EQUITY)
        action_type = CorporateActionType.DIVIDEND
        if schema == "canonical_v5" and any(
            abs(value - ratio) <= ratio * _STOCK_DIVIDEND_ECHO_TOLERANCE
            for ratio in stock_dividend_ratios.get((symbol, timestamp), ())
        ):
            # v5 repeats each stock dividend as a cash_dividends row whose
            # rate is the share ratio, sometimes re-rounded (CBSH 2020-12-01:
            # 0.27 cash plus a 1.05 echo of the 5% stock dividend; CRESY
            # 2023-12-06: 1.0098549 vs 1.00985). The echo is not cash.
            continue
        key = (symbol, timestamp, action_type, value)
        if key in seen:
            continue
        seen.add(key)
        # Sorted: set order must not change the Decimal division order.
        for split_timestamp, ratio in sorted(splits.get(symbol, ())):
            if split_timestamp > timestamp:
                value /= ratio
        actions.append(
            CorporateAction(
                timestamp,
                raw_symbol if schema == "legacy" else symbol,
                action_type,
                float(value),
            )
        )
    return actions


def build_product_universe(
    config: Config,
    bars: list[Bar],
    *,
    market: Market,
    end: datetime | None = None,
    timeframe: str | None = None,
) -> PointInTimeUniverse:
    """Build the point-in-time universe for a product backtest.

    The static configured equity universe: each configured symbol joins at
    its first bar.

    ``timeframe`` defaults to the configured strategy's timeframe and must be
    passed when a run overrides the strategy.
    """
    _require_equity(market)
    selection = config.strategy.equity
    strategy = portfolio_strategy_from(
        selection.name,
        selection.parameters,
        # The configured strategy owns the observation-ranker timeframe. The
        # explicit ``timeframe`` argument filters the bars for a caller's
        # prepared run; passing it into the factory would make a synthetic
        # runtime override fail before the test/runtime can supply its own
        # validated strategy identity.
        timeframe=selection.timeframe,
    )
    selected_timeframe = timeframe or strategy.timeframe
    available = [
        bar
        for bar in bars
        if bar.market == market and bar.timeframe == selected_timeframe and (end is None or bar.timestamp <= end)
    ]
    return build_static_equity_point_in_time_universe(available, config.universe.equity)


def default_evaluation_start(
    bars: list[Bar],
    config: Config,
    minimum_history_bars: int,
    decision_symbols: tuple[str, ...] | None = None,
    *,
    strict: bool = False,
) -> datetime | None:
    """First session on which every symbol the strategy reads has a full warmup.

    ``bars`` are already filtered to the run's market/timeframe/end. Returns
    the timestamp of the ``minimum_history_bars``-th bar of the latest-starting
    required symbol, so the evaluation window opens on the first close the
    strategy can decide on. The required symbols are the strategy's declared
    ``decision_symbols`` (live warms up on those alone, so a short benchmark
    series the strategy never reads cannot delay the window); ``None`` falls
    back to every configured universe symbol. Returns ``None`` (evaluate
    everything, with a warning) when a required symbol is absent or shorter
    than its warmup. ``strict`` (product runs) raises ``ValueError`` instead.
    """
    by_symbol: dict[str, set[datetime]] = {}
    for bar in bars:
        by_symbol.setdefault(canonical_symbol(bar.symbol, bar.market), set()).add(bar.timestamp)
    configured = decision_symbols if decision_symbols else config.universe.equity.symbols
    symbols = list(dict.fromkeys(canonical_symbol(symbol, Market.EQUITY) for symbol in configured))
    required = max(1, minimum_history_bars)
    starts: list[datetime] = []
    for symbol in symbols:
        timestamps = sorted(by_symbol.get(symbol, ()))
        if len(timestamps) < required:
            if strict:
                raise ValueError(
                    f"Insufficient strategy warmup for {symbol}: {len(timestamps)} of {required} required "
                    "bars before the evaluation window; pass an explicit start= or strict=False to "
                    "evaluate every bar"
                )
            log.warning(
                "No default backtest warmup: %s has %d of %d required bars; every bar is evaluated",
                symbol,
                len(timestamps),
                required,
            )
            return None
        starts.append(timestamps[required - 1])
    return max(starts) if starts else None


_MISSING_SESSION_TOLERANCE = 0.01
_MISSING_SESSION_MINIMUM = 2


def _reject_split_actions(corporate_actions: list[CorporateAction] | None) -> None:
    """Product bars are split-adjusted, so a SPLIT event would apply it twice."""
    split_symbols = sorted(
        {action.symbol for action in corporate_actions or () if action.action_type == CorporateActionType.SPLIT}
    )
    if split_symbols:
        raise ValueError(
            "Product backtests run on split-adjusted bars; SPLIT corporate actions would apply the "
            f"split a second time (symbols: {', '.join(split_symbols)}). Supply dividends only; split "
            "events belong to engine runs on raw, unadjusted bars."
        )


def _require_complete_daily_sessions(
    bars: list[Bar],
    *,
    market: Market,
    timeframe: str,
    symbols: tuple[str, ...] | None,
) -> None:
    """Fail closed on gappy daily history for the symbols a strategy reads.

    Each symbol is compared with the NYSE sessions between its own first and
    last daily bar, so a listing that begins later is not a gap. The run fails
    only when at least two sessions and more than 1% of the range are missing.
    """
    if timeframe != "1d":
        return
    dates: dict[str, set[date]] = {}
    for bar in bars:
        if bar.market != market or bar.timeframe != timeframe:
            continue
        dates.setdefault(canonical_symbol(bar.symbol, bar.market), set()).add(bar.timestamp.astimezone(NEW_YORK).date())
    checked = (
        dict.fromkeys(canonical_symbol(symbol, Market.EQUITY) for symbol in symbols)
        if symbols
        else dict.fromkeys(dates)
    )
    for symbol in checked:
        present = dates.get(symbol)
        if not present:
            # A decision symbol with no bars is reported by the warmup check.
            continue
        first, last = min(present), max(present)
        try:
            sessions = [day for day, _, _ in nyse_sessions(first, last)]
        except Exception as exc:
            raise ValueError(f"NYSE calendar unavailable; cannot check {symbol} for missing sessions: {exc}") from exc
        missing = [day for day in sessions if day not in present]
        if len(missing) >= _MISSING_SESSION_MINIMUM and len(missing) > _MISSING_SESSION_TOLERANCE * len(sessions):
            raise ValueError(
                f"{symbol} is missing {len(missing)} of {len(sessions)} NYSE sessions between "
                f"{first.isoformat()} and {last.isoformat()} (first missing: {missing[0].isoformat()}); "
                "repair the daily history or pass strict=False"
            )


def run_product_backtest(
    config: Config,
    bars: list[Bar],
    *,
    market: Market,
    strategy_name: str | None = None,
    parameters: dict[str, object] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    initial_cash: float | None = None,
    output_dir: str | None = None,
    cost_multiplier: float = 1.0,
    persist_artifacts: bool = True,
    corporate_actions: list[CorporateAction] | None = None,
    universe: PointInTimeUniverse | None = None,
    data_feed: str | None = None,
    prepared_inputs: tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]]] | None = None,
    terminal_lifecycles: Mapping[str, TerminalLifecycle] | None = None,
    build_report: bool = True,
    strict: bool = True,
    cash_rate_series: Mapping[date, float] | None = None,
    cash_rate_spread: float = 0.0,
    cost_bps_per_side: float | None = None,
    fill_lag_sessions: int = 0,
    fill_basis: Literal["open", "close"] = "open",
    allow_same_bar_close: bool = False,
    impact_coefficient: float = 0.0,
) -> ProductBacktestResult:
    """Run the product strategy over ``bars``.

    ``strict`` (default) fails closed: a missing strategy warmup, a NYSE
    calendar failure, or a gappy daily history for a decision symbol raises
    ``ValueError``. ``strict=False`` keeps the degraded behaviour (every bar is
    evaluated with a warning, weekday calendar fallback, no gap check).

    The execution options all default to the established model (fills at the
    next bar's open, configured slippage plus half-spread, no idle-cash
    interest, no impact). ``cost_bps_per_side`` is scaled by ``cost_multiplier``
    like the configured costs. See ``PortfolioBacktestEngine`` for semantics.
    """
    if cost_multiplier <= 0:
        raise ValueError("cost_multiplier must be positive")
    _require_equity(market)
    _reject_split_actions(corporate_actions)
    selection = config.strategy.equity
    # Explicit None-check: {} means "use requested strategy defaults", None means "use configured selection"
    # This keeps an explicitly requested strategy's defaults from inheriting
    # the configured selection parameters.
    strategy = portfolio_strategy_from(
        strategy_name or selection.name,
        parameters if parameters is not None else selection.parameters,
        timeframe=selection.timeframe,
    )
    evaluation_start_source = "requested" if start is not None or prepared_inputs is not None else "full history"
    if prepared_inputs is None:
        available = [
            bar
            for bar in bars
            if bar.market == market and bar.timeframe == strategy.timeframe and (end is None or bar.timestamp <= end)
        ]
        if start is None:
            # Without an explicit start the bars before the strategy can first
            # decide are warmup, not forced-cash evaluation sessions that
            # dilute CAGR/Sharpe.
            start = default_evaluation_start(
                available,
                config,
                strategy.minimum_history_bars,
                strategy.decision_symbols,
                strict=strict,
            )
            if start is not None:
                evaluation_start_source = f"first bar + {strategy.minimum_history_bars}-bar strategy warmup"
        if strict:
            _require_complete_daily_sessions(
                available,
                market=market,
                timeframe=strategy.timeframe,
                symbols=strategy.decision_symbols,
            )
        filtered = [bar for bar in available if start is None or bar.timestamp >= start]
        if not filtered:
            raise ValueError(f"No {market.value} {strategy.timeframe} bars in the requested period")
        warmup = _normalized_warmup([bar for bar in available if start is not None and bar.timestamp < start])
        if universe is None:
            universe = build_product_universe(
                config,
                bars,
                market=market,
                end=end,
                timeframe=strategy.timeframe,
            )
        prepared = None
    else:
        filtered, warmup, (ordered, decision_bars) = prepared_inputs
        if not filtered:
            raise ValueError(f"No {market.value} {strategy.timeframe} bars in the requested period")
        if universe is None:
            raise ValueError("prepared_inputs requires an explicit universe")
        prepared = (ordered, decision_bars)
    risk = portfolio_risk_limits_from_config(config.risk)

    universe_hash = hash_point_in_time_universe(universe)
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=initial_cash if initial_cash is not None else config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * cost_multiplier,
        spread_bps=config.backtest.spread_bps * cost_multiplier,
        commission=config.backtest.commission,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        equity_regulatory_fee_multiplier=cost_multiplier,
        universe=universe,
        universe_version=universe_hash,
        risk_limits=risk,
        maximum_allocation=selection.maximum_allocation,
        execution_policy=portfolio_execution_policy_from_config(config.execution),
        terminal_lifecycles=terminal_lifecycles,
        cash_rate_series=cash_rate_series,
        cash_rate_spread=cash_rate_spread,
        cost_bps_per_side=(None if cost_bps_per_side is None else cost_bps_per_side * cost_multiplier),
        fill_lag_sessions=fill_lag_sessions,
        fill_basis=fill_basis,
        allow_same_bar_close=allow_same_bar_close,
        impact_coefficient=impact_coefficient,
    )
    result = engine.run(
        filtered,
        warmup=warmup,
        corporate_actions=corporate_actions,
        prepared_inputs=prepared,
    )
    if build_report:
        report = build_portfolio_report(
            result,
            strategy_name=strategy.name,
            strategy_version=strategy.version,
        )
    else:
        report = PortfolioReport(
            summary={
                "strategy_name": strategy.name,
                "strategy_version": strategy.version,
                "profit_factor": _profit_factor(result.closed_position_events),
                "number_of_trades": len(result.closed_position_events),
            },
            rebalances=(),
            fills=(),
            weight_history=(),
            universe_history=(),
            monthly_returns={},
            yearly_returns=(),
            regime_performance={},
            rolling_beta=(),
            per_symbol=(),
            equity_curve=(),
            drawdowns=(),
        )
    report.summary.update(
        {
            "market": market.value,
            "strategy_parameters": dict(strategy.parameter_tuple()),
            "fill_model": "decision on closed bar; next tradable bar open",
            "evaluation_start_source": evaluation_start_source,
            "price_adjustment_model": _price_adjustment_model(corporate_actions),
            "cost_multiplier": cost_multiplier,
            "universe_hash": universe_hash,
            "universe_methodology": universe.methodology,
            "scanner_versions": sorted({snapshot.scanner_version for snapshot in universe.snapshots}),
            "data_feed": (data_feed or "unknown (no provider feed was reported for this run)"),
            "data_feed_note": _data_feed_note(data_feed),
            "survivorship_bias_limitation": (
                "Candidate metadata may reflect currently supported Alpaca assets; historical "
                "scanner membership is point-in-time within the supplied bar set, but delisted "
                "assets cannot be reconstructed when the provider omits them."
            ),
            "regulatory_fee_model": {
                "equity_taf_per_share": config.backtest.equity_taf_per_share,
                "equity_taf_cap": config.backtest.equity_taf_cap,
                "equity_cat_per_share": config.backtest.equity_cat_per_share,
                "equity_sec_fee_rate": config.backtest.equity_sec_fee_rate,
                "equity_use_effective_dated_fees": config.backtest.equity_use_effective_dated_fees,
                "cost_multiplier": cost_multiplier,
                "note": (
                    "SEC Section 31, FINRA TAF and CAT fees are approximated per "
                    "trade date from the official rate schedules when "
                    "equity_use_effective_dated_fees is enabled; otherwise the "
                    "configured fixed current regulatory rates are used. Either "
                    "way this is a fixed-rate approximation, not exact "
                    "historical fees — verify the rates in force at the trade "
                    "date before using results as a cost proxy."
                ),
            },
            "drawdown_halt_shadow_events": {
                "count": len(result.drawdown_halt_shadow_events),
                "first": (
                    result.drawdown_halt_shadow_events[0][0].isoformat() if result.drawdown_halt_shadow_events else None
                ),
                "last": (
                    result.drawdown_halt_shadow_events[-1][0].isoformat()
                    if result.drawdown_halt_shadow_events
                    else None
                ),
                "note": (
                    "Offline backtest: entries were NOT blocked at these "
                    "points (the curve kept trading). Under the production "
                    "drawdown policy each event is a halt until an operator "
                    "starts a new risk epoch."
                ),
            },
        }
    )
    execution_options = _execution_options_summary(
        result,
        cash_rate_series=cash_rate_series,
        cash_rate_spread=cash_rate_spread,
        cost_bps_per_side=(None if cost_bps_per_side is None else cost_bps_per_side * cost_multiplier),
        fill_lag_sessions=fill_lag_sessions,
        fill_basis=fill_basis,
        allow_same_bar_close=allow_same_bar_close,
        impact_coefficient=impact_coefficient,
    )
    if execution_options is not None:
        report.summary["execution_options"] = execution_options
    run_id = uuid4().hex
    destination = Path(output_dir or config.backtest.output_dir) / run_id
    if persist_artifacts:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "report.json").write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )
        report.write_csvs(str(destination))
        (destination / "universe.json").write_text(
            json.dumps(universe.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    score = ranking_score(result, config.backtest.ranking_metric)
    return ProductBacktestResult(run_id, result, report, universe, str(destination), score)


def _execution_options_summary(
    result: PortfolioBacktestResult,
    *,
    cash_rate_series: Mapping[date, float] | None,
    cash_rate_spread: float,
    cost_bps_per_side: float | None,
    fill_lag_sessions: int,
    fill_basis: str,
    allow_same_bar_close: bool,
    impact_coefficient: float,
) -> dict[str, object] | None:
    """Report block for non-default execution options; None when all default."""
    if (
        cash_rate_series is None
        and cost_bps_per_side is None
        and fill_lag_sessions == 0
        and fill_basis == "open"
        and not allow_same_bar_close
        and impact_coefficient == 0.0
    ):
        return None
    return {
        "cash_rate_series_supplied": cash_rate_series is not None,
        "cash_rate_spread": cash_rate_spread,
        "cash_interest": result.cash_interest,
        "cost_bps_per_side": cost_bps_per_side,
        "fill_lag_sessions": fill_lag_sessions,
        "fill_basis": fill_basis,
        "allow_same_bar_close": allow_same_bar_close,
        "impact_coefficient": impact_coefficient,
    }


def _run_metrics(run: ProductBacktestResult) -> dict[str, object]:
    portfolio = run.result
    years = portfolio.duration_days / 365.0
    sells = sum(1 for fill in portfolio.fills if fill.side == OrderSide.SELL)
    return {
        "ending_equity": portfolio.ending_equity,
        "total_return": portfolio.total_return,
        "cagr": portfolio.cagr,
        "max_drawdown": portfolio.max_drawdown,
        "sells_per_year": sells / years if years > 0 else 0.0,
        "turnover": portfolio.turnover,
        "fee_total": portfolio.fee_total,
        "slippage_cost_total": portfolio.slippage_cost_total,
    }


def cost_sensitivity(
    config: Config,
    bars: list[Bar],
    *,
    market: Market,
    grid: tuple[float, ...] = (0.0, 10.0, 25.0),
    multipliers: tuple[float, ...] = (2.0,),
    **run_kwargs: Any,
) -> list[dict[str, object]]:
    """Rerun the product backtest under alternative cost assumptions.

    The first row is the configured baseline. Each ``grid`` value replaces the
    adverse cost with that many bps per side (regulatory fees unchanged). Each
    ``multipliers`` value scales the configured slippage, spread and fees.
    Artifacts are not persisted. ``run_kwargs`` are forwarded unchanged.
    """
    reserved = ("cost_multiplier", "cost_bps_per_side", "persist_artifacts", "build_report")
    conflicts = [name for name in reserved if name in run_kwargs]
    if conflicts:
        raise ValueError(f"cost_sensitivity controls {', '.join(conflicts)}; do not pass it")
    settings: list[tuple[str, dict[str, object]]] = [("baseline", {})]
    settings += [(f"{bps:g} bps per side", {"cost_bps_per_side": float(bps)}) for bps in grid]
    settings += [(f"{factor:g}x configured costs", {"cost_multiplier": float(factor)}) for factor in multipliers]
    rows: list[dict[str, object]] = []
    for label, overrides in settings:
        run = run_product_backtest(
            config,
            bars,
            market=market,
            persist_artifacts=False,
            build_report=False,
            **{**run_kwargs, **overrides},
        )
        rows.append({"setting": label, **_run_metrics(run)})
    return rows


# (scenario, fill options). ``same_close`` is the research-only signal-close
# fill: an upper bound on what a decision made from the bar's own close could
# have earned, not a tradable result.
FILL_TIMING_SCENARIOS: tuple[tuple[str, dict[str, object]], ...] = (
    ("same_close", {"fill_lag_sessions": 0, "fill_basis": "close", "allow_same_bar_close": True}),
    ("next_open", {"fill_lag_sessions": 0, "fill_basis": "open", "allow_same_bar_close": False}),
    ("next_close", {"fill_lag_sessions": 0, "fill_basis": "close", "allow_same_bar_close": False}),
    ("two_session_delay", {"fill_lag_sessions": 1, "fill_basis": "open", "allow_same_bar_close": False}),
)


def fill_timing_report(
    config: Config,
    bars: list[Bar],
    *,
    market: Market,
    **run_kwargs: Any,
) -> dict[str, dict[str, object]]:
    """Compare fill timing: same-bar close, next open, next close, 2-session delay.

    ``two_session_delay`` fills at the open two sessions after the decision bar
    (``fill_lag_sessions=1`` on top of the next-open fill). Keys per scenario:
    the fill options plus the ``_run_metrics`` fields.
    """
    reserved = ("fill_lag_sessions", "fill_basis", "allow_same_bar_close", "persist_artifacts", "build_report")
    conflicts = [name for name in reserved if name in run_kwargs]
    if conflicts:
        raise ValueError(f"fill_timing_report controls {', '.join(conflicts)}; do not pass it")
    report: dict[str, dict[str, object]] = {}
    for name, options in FILL_TIMING_SCENARIOS:
        run = run_product_backtest(
            config,
            bars,
            market=market,
            persist_artifacts=False,
            build_report=False,
            **{**run_kwargs, **options},
        )
        report[name] = {**options, **_run_metrics(run)}
    return report


def optimize_grid(
    config: Config,
    bars: list[Bar],
    *,
    market: Market,
    grid: dict[str, list[object]],
    strategy_name: str | None = None,
    base_parameters: dict[str, object] | None = None,
    initial_cash: float | None = None,
    train_start: datetime | None = None,
    train_end: datetime | None = None,
    oos_start: datetime | None = None,
    oos_end: datetime | None = None,
    output_dir: str | None = None,
    corporate_actions: list[CorporateAction] | None = None,
    data_feed: str | None = None,
    strict: bool = True,
) -> dict[str, object]:
    if not grid or any(not values for values in grid.values()):
        raise ValueError("Optimization grid must contain at least one value per parameter")
    _validate_optimization_windows(train_start, train_end, oos_start, oos_end)
    combination_count = math.prod(len(values) for values in grid.values())
    if combination_count > _MAX_OPTIMIZATION_COMBINATIONS:
        raise ValueError(
            f"Optimization grid has {combination_count} combinations; maximum is {_MAX_OPTIMIZATION_COMBINATIONS}"
        )
    _require_equity(market)
    _reject_split_actions(corporate_actions)
    selection = config.strategy.equity
    baseline = dict(base_parameters or selection.parameters)
    keys = tuple(sorted(grid))
    strategy_timeframe = portfolio_strategy_from(
        strategy_name or selection.name,
        parameters=baseline,
        timeframe=selection.timeframe,
    ).timeframe
    # Same warmup rule as ``run_product_backtest``: bars before the longest
    # candidate's first decidable session are warmup, over the symbols any
    # candidate reads (every configured symbol as soon as one candidate does
    # not narrow its decision symbols).
    candidates = [
        portfolio_strategy_from(
            strategy_name or selection.name,
            parameters={**baseline, **dict(zip(keys, values, strict=True))},
            timeframe=selection.timeframe,
        )
        for values in itertools.product(*(grid[key] for key in keys))
    ]
    declared = [candidate.decision_symbols for candidate in candidates]
    decision_symbols = (
        None
        if any(symbols is None for symbols in declared)
        else tuple(dict.fromkeys(symbol for symbols in declared if symbols is not None for symbol in symbols))
    )
    if strict:
        # Once per optimization: the trials below run on prepared inputs and
        # would otherwise skip the calendar check.
        _require_complete_daily_sessions(
            bars,
            market=market,
            timeframe=strategy_timeframe,
            symbols=decision_symbols,
        )
    if train_start is None:
        train_start = default_evaluation_start(
            [bar for bar in bars if bar.market == market and bar.timeframe == strategy_timeframe],
            config,
            max(candidate.minimum_history_bars for candidate in candidates),
            decision_symbols,
            strict=strict,
        )
    # The eligible pool and requested baseline observation basis are
    # trial-invariant: build them once per window and reuse them so candidate
    # parameters cannot silently redefine streamed membership between trials.
    train_universe = build_product_universe(
        config,
        bars,
        market=market,
        end=train_end,
        timeframe=strategy_timeframe,
    )
    # The per-trial bar preprocessing (window filter, dedup, sort) is likewise
    # trial-invariant: prepare it once per window and reuse it.
    train_prepared = _prepared_window(bars, market, strategy_timeframe, train_start, train_end)
    ranked: list[dict[str, object]] = []
    for values in itertools.product(*(grid[key] for key in keys)):
        parameters = {**baseline, **dict(zip(keys, values, strict=True))}
        run = run_product_backtest(
            config,
            bars,
            market=market,
            strategy_name=strategy_name,
            parameters=parameters,
            start=train_start,
            end=train_end,
            initial_cash=initial_cash,
            output_dir=output_dir,
            persist_artifacts=False,
            corporate_actions=corporate_actions,
            data_feed=data_feed,
            universe=train_universe,
            prepared_inputs=train_prepared,
            build_report=False,
            strict=strict,
        )
        result = run.result
        ranked.append(
            {
                "parameters": parameters,
                "score": run.score,
                "return": result.total_return,
                "cagr": result.cagr,
                "sharpe": result.sharpe,
                "sortino": result.sortino,
                "max_drawdown": result.max_drawdown,
                "profit_factor": run.report.summary["profit_factor"],
                "trade_count": run.report.summary["number_of_trades"],
                "run_id": run.run_id,
            }
        )
    ranked.sort(key=_optimization_sort_key)
    payload: dict[str, object] = {
        "training_results": ranked,
        "ranking": config.backtest.ranking_metric,
        "combination_count": combination_count,
    }
    if oos_start is not None and oos_end is not None:
        best = ranked[0]
        best_parameters = best["parameters"]
        if not isinstance(best_parameters, dict):
            raise TypeError("Optimization parameters must be a mapping")
        oos_universe = build_product_universe(
            config,
            bars,
            market=market,
            end=oos_end,
            timeframe=strategy_timeframe,
        )
        oos_prepared = _prepared_window(bars, market, strategy_timeframe, oos_start, oos_end)
        oos = run_product_backtest(
            config,
            bars,
            market=market,
            strategy_name=strategy_name,
            parameters=dict(best_parameters),
            start=oos_start,
            end=oos_end,
            initial_cash=initial_cash,
            output_dir=output_dir,
            corporate_actions=corporate_actions,
            data_feed=data_feed,
            universe=oos_universe,
            prepared_inputs=oos_prepared,
            strict=strict,
        )
        payload["out_of_sample"] = {
            "parameters": best["parameters"],
            "run_id": oos.run_id,
            "summary": oos.report.summary,
            "output_dir": oos.output_dir,
        }
    destination = Path(output_dir or config.backtest.output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "optimization.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    payload["output"] = str(path)
    return payload


def _profit_factor(closed_position_events: tuple[tuple[datetime, str, float], ...]) -> float | None:
    wins = sum(pnl for _, _, pnl in closed_position_events if pnl > 0)
    losses = abs(sum(pnl for _, _, pnl in closed_position_events if pnl < 0))
    return wins / losses if losses > 0 else None


def _price_adjustment_model(corporate_actions: list[CorporateAction] | None) -> str:
    """What the run's prices and cash flows actually were."""
    if corporate_actions:
        return (
            "split-adjusted execution/signal prices with explicit dividend cash flows from the "
            "supplied corporate actions; callers must not supply split actions for already "
            "split-adjusted bars"
        )
    return (
        "prices as supplied (downloads are split-adjusted) with no dividend cash flows: no "
        "corporate actions were supplied, so strategy and benchmark returns are price-only"
    )


def _data_feed_note(data_feed: str | None) -> str:
    """Provenance caveat matching the feed the run actually received.

    Claiming the consolidated SIP tape for an unverified offline download
    would put a false provenance claim in a persisted research artifact.
    """
    normalized = (data_feed or "").lower()
    if normalized == "iex":
        return (
            "IEX historical data reflects one exchange's prints, not the "
            "consolidated SIP/NBBO tape; liquidity, spread and volume "
            "proxies derived from IEX understate full-market conditions."
        )
    if normalized == "sip":
        return "SIP consolidates the national market system tape."
    return "Feed unknown; provenance not verified for this offline run."


def _prepared_window(
    bars: list[Bar],
    market: Market,
    timeframe: str,
    start: datetime | None,
    end: datetime | None,
) -> tuple[list[Bar], list[Bar], tuple[list[Bar], list[Bar]]]:
    """Precompute the per-window engine inputs for a backtest trial.

    Mirrors ``run_product_backtest``'s window filter plus the engine's
    deterministic dedup/sort, so every trial of an optimization grid can
    reuse one preparation. Returns ``(filtered, warmup, (ordered,
    decision_bars))`` exactly as ``engine.run(prepared_inputs=...)`` expects.
    """
    _require_equity(market)
    available = [
        bar
        for bar in bars
        if bar.market == market and bar.timeframe == timeframe and (end is None or bar.timestamp <= end)
    ]
    filtered = [bar for bar in available if start is None or bar.timestamp >= start]
    warmup = _normalized_warmup([bar for bar in available if start is not None and bar.timestamp < start])
    ordered = decision_bars = sorted(
        PortfolioBacktestEngine._deduplicate(filtered),
        key=lambda bar: (bar.timestamp, bar.symbol),
    )
    return filtered, warmup, (ordered, decision_bars)


def _normalized_warmup(warmup: list[Bar]) -> list[Bar]:
    """Canonicalize and timestamp-sort warmup bars for ``engine.run``.

    The engine ingests warmup in the given order and keeps only bars strictly
    newer than each series tail, so unsorted or alias-symbol warmup would be
    silently dropped. Both the direct and the prepared (optimizer) paths use
    this so identical parameters see identical warmup history.
    """
    canon_warmup: list[Bar] = []
    for bar in warmup:
        can = canonical_symbol(bar.symbol, bar.market)
        if can != bar.symbol:
            bar = Bar(
                timestamp=bar.timestamp,
                symbol=can,
                timeframe=bar.timeframe,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                market=bar.market,
            )
        canon_warmup.append(bar)
    return sorted(canon_warmup, key=lambda b: (b.timestamp, b.symbol))


def _optimization_sort_key(item: dict[str, object]) -> tuple[float, str]:
    score = item.get("score")
    numeric_score = float(score) if isinstance(score, (int, float)) else float("-inf")
    parameters = item.get("parameters")
    return -numeric_score, json.dumps(parameters, sort_keys=True)


def ranking_score(result: PortfolioBacktestResult, metric: str = "risk_adjusted") -> float:
    if metric == "sharpe":
        return result.sharpe
    if metric == "sortino":
        return result.sortino
    if metric == "calmar":
        return result.calmar
    if metric != "risk_adjusted":
        raise ValueError(f"Unsupported ranking metric: {metric}")
    trade_penalty = min(0.0, (len(result.closed_position_events) - 5) * 0.10)
    return result.sharpe + 0.50 * result.sortino + 0.25 * result.calmar - 2.0 * result.max_drawdown + trade_penalty


def _validate_optimization_windows(
    train_start: datetime | None,
    train_end: datetime | None,
    oos_start: datetime | None,
    oos_end: datetime | None,
) -> None:
    if (train_start is None) != (train_end is None):
        raise ValueError("TRAIN requires both start and end")
    if (oos_start is None) != (oos_end is None):
        raise ValueError("OOS requires both start and end")
    if oos_start is not None and (train_start is None or train_end is None):
        raise ValueError("OOS evaluation requires an explicit TRAIN window")
    if train_start is not None and train_end is not None and train_start >= train_end:
        raise ValueError("TRAIN start must be before TRAIN end")
    if oos_start is not None and oos_end is not None and oos_start >= oos_end:
        raise ValueError("OOS start must be before OOS end")
    if train_end is not None and oos_start is not None and train_end >= oos_start:
        raise ValueError("TRAIN and OOS windows must not overlap")


__all__ = [
    "ProductBacktestResult",
    "build_product_universe",
    "default_evaluation_start",
    "load_csv_bars",
    "load_csv_corporate_actions",
    "optimize_grid",
    "ranking_score",
    "run_product_backtest",
]
