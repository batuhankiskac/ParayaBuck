#!/usr/bin/env python3
"""ParayaBuck strategy research harness (diagnostic only — NOT a product fork).

Reuses production code paths directly:
  * strategy logic           -> parayabuck.portfolio (equity_dynamic_trend_1d_v1)
  * historical universe      -> build_product_universe (production scanner policy,
                                point-in-time, strictly trailing data)
  * risk overlay             -> PortfolioRiskOverlay / PortfolioRiskLimits
  * execution policy         -> PortfolioExecutionPolicy (shared band/notional)
  * cost model               -> PortfolioBacktestEngine cost inputs
                                (slippage/spread/effective-dated fees)

It does NOT simulate anything itself: it wraps the production engine with
instrumentation and runs production configuration.

Modes
-----
  baseline   production-policy backtest (identical to the CLI backtest path)
  alpha      NON-DEPLOYABLE ALPHA DIAGNOSTIC: daily-loss / drawdown entry
             latches disabled via risk-limit sentinels; every other limit,
             cost, and the strategy itself stay unchanged
  incidents  operational breaker incident log + recovery counterfactual
  exposure   per-day cash/exposure decomposition (Phase 4 attribution)
  signal     signal-level forward-return diagnostics (rank IC, top-N excess)
  benchmarks SPY / 50% SPY+cash / risk-matched SPY benchmarks
  all        run baseline + alpha + incidents + exposure + signal

Usage
-----
  python scripts/strategy_diagnose.py --mode all --start 2020-01-02 --end 2024-12-31
  python scripts/strategy_diagnose.py --mode alpha --start 2025-01-02 --end 2026-07-30
  python scripts/strategy_diagnose.py --mode signal --start 2020-01-02 --end 2024-12-31

Outputs go to reports/research/<mode>/ (gitignored). Summary JSON is printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.product import build_product_universe, load_csv_bars
from parayabuck.config import load_config
from parayabuck.core.models import Market
from parayabuck.execution.portfolio import (
    PortfolioExecutionPolicy,
    PortfolioRiskLimits,
    portfolio_risk_limits_from_config,
)
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.momentum import momentum_at

REPO = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = REPO / "reports" / "research"

ALPHA_DIAGNOSTIC_LABEL = "NON-DEPLOYABLE ALPHA DIAGNOSTIC"
HORIZONS = (5, 21, 63)

CASH_REASONS = (
    "DRAWDOWN_LATCH",
    "DAILY_LOSS_LATCH",
    "VOLATILITY_SCALING",
    "STRATEGY_CASH",
    "MAXIMUM_ALLOCATION",
    "POSITION_CAPS",
    "NO_QUALIFYING_SYMBOLS",
    "MARKET_OR_UNIVERSE_NOT_READY",
    "INTERMEDIATE_RISK",
    "OTHER",
)


# --------------------------------------------------------------------------
# Instrumentation
# --------------------------------------------------------------------------


@dataclass
class DecisionRecord:
    timestamp: datetime
    scheduled: bool
    intermediate_risk: bool
    reason: str
    qualified_symbols: int
    selected_symbols: tuple[str, ...]
    risk_multiplier: float
    target_weights_pre_overlay: dict[str, float]
    target_cash_weight_pre_overlay: float
    target_weights_post_overlay: dict[str, float]
    target_cash_weight_post_overlay: float
    overlay_halted: bool
    overlay_reasons: tuple[str, ...]
    equity_at_decision: float
    peak_equity: float
    drawdown_pct: float
    daily_loss_pct: float
    positions: tuple[tuple[str, float], ...]
    universe_symbols: tuple[str, ...]


@dataclass
class DailyRow:
    date: date
    equity: float
    cash_ratio: float
    gross_exposure: float
    positions_count: int
    reasons: tuple[str, ...]
    decision_date: date | None


class Recorder:
    """Collects per-decision observations from instrumented strategy + engine."""

    def __init__(self) -> None:
        self.decisions: list[DecisionRecord] = []
        self.skips: list[dict[str, Any]] = []
        self.daily: list[DailyRow] = []
        self._pending: dict[str, Any] = {}
        self._last_recorded: datetime | None = None

    def observe_strategy(self, context: PortfolioContext, target: TargetPortfolio) -> None:
        self._pending = {
            "timestamp": context.decision_timestamp,
            "qualified": int(target.metadata.get("qualified_symbols", 0)),
            "selected": tuple(str(s) for s in target.metadata.get("selected_symbols", ())),
            "risk_multiplier": float(getattr(target, "risk_multiplier", 1.0) or 1.0),
            "weights_pre": {w.symbol: w.weight for w in target.weights},
            "cash_pre": float(target.cash_weight),
        }

    def observe_skip(
        self,
        timestamp: datetime,
        scheduled: bool,
        reason: str,
        universe_symbols: tuple[str, ...],
    ) -> None:
        self.skips.append(
            {
                "timestamp": timestamp.isoformat(),
                "scheduled": scheduled,
                "reason": reason,
                "universe_symbols": list(universe_symbols),
            }
        )

    def observe_post_decision(self, engine: PortfolioBacktestEngine) -> None:
        record = engine._rebalances[-1] if engine._rebalances else None
        if record is None:
            return
        if self._last_recorded is not None and record.decision_timestamp <= self._last_recorded:
            return  # decision was skipped inside production; record unchanged
        self._last_recorded = record.decision_timestamp
        pending = self._pending
        self._pending = {}
        snapshot = engine._account_snapshot(record.decision_timestamp)
        equity = snapshot.equity
        peak = engine._peak_equity
        positions = tuple(sorted((p.symbol, p.qty) for p in snapshot.positions if p.qty > 0))
        scheduled = self._is_period_start(record.decision_timestamp)
        self.decisions.append(
            DecisionRecord(
                timestamp=record.decision_timestamp,
                scheduled=scheduled,
                intermediate_risk=not scheduled,
                reason=record.reason,
                qualified_symbols=int(pending.get("qualified", 0)),
                selected_symbols=tuple(pending.get("selected", ())),
                risk_multiplier=float(pending.get("risk_multiplier", 1.0)),
                target_weights_pre_overlay=dict(pending.get("weights_pre", {})),
                target_cash_weight_pre_overlay=float(pending.get("cash_pre", 1.0)),
                target_weights_post_overlay=dict(record.target_weights),
                target_cash_weight_post_overlay=float(record.target_cash_weight),
                overlay_halted=record.risk_overlay_halted,
                overlay_reasons=tuple(record.risk_overlay_reasons),
                equity_at_decision=float(record.equity_at_decision),
                peak_equity=float(peak),
                drawdown_pct=max(0.0, (peak - equity) / peak) if peak > 0 else 0.0,
                daily_loss_pct=max(0.0, snapshot.daily_pnl / equity) if equity > 0 else 0.0,
                positions=positions,
                universe_symbols=tuple(
                    sorted(s for s in engine._universe_symbols(record.decision_timestamp) or ())
                ),
            )
        )

    def _is_period_start(self, timestamp: datetime) -> bool:
        if not self.decisions:
            return True
        previous = self.decisions[-1].timestamp
        return previous.isocalendar()[:2] != timestamp.isocalendar()[:2]

    def finish(self, engine: PortfolioBacktestEngine, result: Any) -> None:
        """Capture per-day cash ratios + equity curve for exposure attribution."""
        timestamps = list(result.curve_timestamps)
        equity = list(result.equity_curve)
        holdings = list(getattr(engine, "_holdings_history", []))
        for i, ts in enumerate(timestamps):
            count, cash_ratio, _concentration = holdings[i] if i < len(holdings) else (0, 1.0, 0.0)
            self.daily.append(
                DailyRow(
                    date=ts.date(),
                    equity=equity[i],
                    cash_ratio=cash_ratio,
                    gross_exposure=1.0 - cash_ratio,
                    positions_count=int(count),
                    reasons=(),
                    decision_date=None,
                )
            )


class InstrumentedStrategy(PortfolioStrategy):
    """Generic production-strategy wrapper with a decision observation hook.

    Delegates every attribute to the wrapped production strategy; the only
    behavioral addition is the observation callback. No strategy logic is
    changed or duplicated.
    """

    def __init__(
        self,
        inner: PortfolioStrategy,
        *,
        recorder: Recorder | None = None,
    ) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_recorder", recorder)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _validate_parameters(self) -> None:
        return None  # delegated to the inner strategy at construction

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        target = self._inner.decide(context)
        if self._recorder is not None:
            self._recorder.observe_strategy(context, target)
        return target


class InstrumentedEngine(PortfolioBacktestEngine):
    """Production engine with decision/skip observation hooks."""

    def __init__(self, *args: Any, recorder: Recorder | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._recorder = recorder

    def _decide(self, timestamp: datetime) -> None:  # type: ignore[override]
        # Observe skips before production runs, then observe the outcome after.
        market = self.strategy.market
        previous = self._previous_timestamp[market]
        scheduled = self.rebalance_policy.is_period_start(previous, timestamp)
        context = self._build_context(timestamp)
        skip_reason: str | None = None
        if not self.strategy.supports(context):
            skip_reason = "supports_gate"
        else:
            expected = self._universe_symbols(timestamp)
            if expected is not None and any(
                symbol not in context.history
                or not context.history[symbol]
                or context.history[symbol][-1].timestamp != timestamp
                for symbol in expected
            ):
                skip_reason = "universe_batch_incomplete"
        if skip_reason is not None and self._recorder is not None:
            self._recorder.observe_skip(
                timestamp,
                scheduled,
                skip_reason,
                tuple(sorted(context.history)),
            )
        super()._decide(timestamp)
        if self._recorder is not None:
            self._recorder.observe_post_decision(self)


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


def build_engine(
    config: Any,
    *,
    market: Market,
    strategy: PortfolioStrategy,
    universe: Any,
    initial_cash: float,
    cost_multiplier: float,
    disable_operational_latches: bool,
    recorder: Recorder | None,
) -> PortfolioBacktestEngine:
    """Construct the production engine; optionally disable the two latches."""
    base = portfolio_risk_limits_from_config(config.risk)
    if disable_operational_latches:
        limits = PortfolioRiskLimits(
            daily_loss_limit=1.0,  # 100% sentinel: never tripped
            drawdown_halt=1.0,  # 100% sentinel: never tripped
            maximum_gross=base.maximum_gross,
            maximum_equity=base.maximum_equity,
            maximum_crypto=base.maximum_crypto,
            maximum_equity_asset=base.maximum_equity_asset,
            maximum_crypto_asset=base.maximum_crypto_asset,
            minimum_cash=base.minimum_cash,
            maximum_positions=base.maximum_positions,
            maximum_crypto_positions=base.maximum_crypto_positions,
        )
    else:
        limits = base
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    return InstrumentedEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=initial_cash,
        slippage_bps=config.backtest.slippage_bps * cost_multiplier,
        spread_bps=config.backtest.spread_bps * cost_multiplier,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * cost_multiplier,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        benchmark_symbol="SPY" if market == Market.EQUITY else "BTC/USD",
        universe=universe,
        universe_version=getattr(universe, "hash", "") or "",
        risk_limits=limits,
        maximum_allocation=selection.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        recorder=recorder,
    )


def run_diagnostic(
    config: Any,
    bars: list[Any],
    *,
    market: Market,
    strategy_name: str,
    parameters: dict[str, object] | None,
    start: datetime,
    end: datetime | None,
    initial_cash: float,
    cost_multiplier: float,
    disable_operational_latches: bool,
    output_dir: Path,
) -> tuple[dict[str, Any], Recorder, Any]:
    """Run the instrumented production backtest for one window."""
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    recorder = Recorder()
    inner = portfolio_strategy_from(
        strategy_name,
        parameters if parameters is not None else selection.parameters,
    )
    strategy = InstrumentedStrategy(inner, recorder=recorder)
    universe = build_product_universe(
        config, bars, market=market, end=end, timeframe=strategy.timeframe
    )
    engine = build_engine(
        config,
        market=market,
        strategy=strategy,
        universe=universe,
        initial_cash=initial_cash or config.backtest.initial_cash,
        cost_multiplier=cost_multiplier,
        disable_operational_latches=disable_operational_latches,
        recorder=recorder,
    )
    available = [
        bar
        for bar in bars
        if bar.market == market
        and bar.timeframe == strategy.timeframe
        and (end is None or bar.timestamp <= end)
    ]
    filtered = [bar for bar in available if start is None or bar.timestamp >= start]
    warmup = [bar for bar in available if start is not None and bar.timestamp < start]
    result = engine.run(filtered, warmup=warmup)
    recorder.finish(engine, result)
    from parayabuck.backtest.portfolio_report import build_portfolio_report

    report = build_portfolio_report(
        result,
        strategy_name=strategy.name,
        strategy_version=strategy.version,
    )
    summary = dict(report.summary)
    summary["fill_model"] = "decision on closed bar; next tradable bar open"
    summary["cost_multiplier"] = cost_multiplier
    summary["universe_hash"] = _universe_hash(universe)
    summary["market"] = market.value
    summary["strategy_parameters"] = dict(strategy.parameter_tuple())
    summary["diagnostic_label"] = (
        ALPHA_DIAGNOSTIC_LABEL if disable_operational_latches else "PRODUCTION POLICY"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(
            {
                "summary": summary,
                "decisions": [d.__dict__ for d in recorder.decisions],
                "skips": recorder.skips,
                "daily": [
                    {
                        "date": d.date.isoformat(),
                        "equity": d.equity,
                        "cash_ratio": d.cash_ratio,
                        "gross_exposure": d.gross_exposure,
                        "positions_count": d.positions_count,
                    }
                    for d in recorder.daily
                ],
            },
            indent=2,
            default=str,
        )
    )
    fills = [
        {
            "timestamp": f.timestamp.isoformat(),
            "symbol": f.symbol,
            "side": f.side.value,
            "qty": f.qty,
            "reference_price": f.reference_price,
            "fill_price": f.fill_price,
            "slippage_cost": f.slippage_cost,
            "spread_cost": f.spread_cost,
            "fee": f.fee,
            "decision_timestamp": f.decision_timestamp.isoformat(),
        }
        for f in result.fills
    ]
    (output_dir / "fills.csv").write_text(
        "\n".join(
            [",".join(fills[0].keys())]
            + [",".join(str(row[k]) for k in fills[0].keys()) for row in fills]
        )
        if fills
        else "timestamp,symbol,side,qty,reference_price,fill_price,slippage_cost,"
        "spread_cost,fee,decision_timestamp"
    )
    return summary, recorder, result


def _universe_hash(universe: Any) -> str:
    raw = getattr(universe, "to_dict", lambda: {})()
    return hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Exposure decomposition (Phase 4)
# --------------------------------------------------------------------------


def decompose_exposure(recorder: Recorder) -> list[dict[str, Any]]:
    """Attribute each day's cash to reasons; returns per-day rows + aggregates."""
    decision_by_date = {d.timestamp.date(): d for d in recorder.decisions}
    scheduled_skip_dates = {
        datetime.fromisoformat(s["timestamp"]).date() for s in recorder.skips if s.get("scheduled")
    }
    rows: list[dict[str, Any]] = []
    last: DecisionRecord | None = None
    for day in recorder.daily:
        reasons: set[str] = set()
        decision = decision_by_date.get(day.date)
        if decision is not None:
            last = decision
        if last is not None:
            active = last
            for reason in active.overlay_reasons:
                if reason == "daily_loss_limit":
                    reasons.add("DAILY_LOSS_LATCH")
                elif reason == "portfolio_drawdown_halt":
                    reasons.add("DRAWDOWN_LATCH")
                elif reason == "exposure_scaled_to_limit":
                    reasons.add("MAXIMUM_ALLOCATION")
                elif reason == "position_count_limit":
                    reasons.add("POSITION_CAPS")
                else:
                    reasons.add("OTHER")
            if active.risk_multiplier < 0.99:
                reasons.add("VOLATILITY_SCALING")
            if active.qualified_symbols == 0 and not active.target_weights_pre_overlay:
                reasons.add("NO_QUALIFYING_SYMBOLS")
            if active.target_cash_weight_pre_overlay > 0.40:
                # Strategy's own caps (6 x 10% max) leave >= 40% cash before
                # any overlay; this is the deliberate design cash.
                reasons.add("STRATEGY_CASH")
            if active.intermediate_risk:
                reasons.add("INTERMEDIATE_RISK")
        if day.date in scheduled_skip_dates:
            reasons.add("MARKET_OR_UNIVERSE_NOT_READY")
        if not reasons:
            reasons.add("OTHER")
        rows.append(
            {
                "date": day.date.isoformat(),
                "equity": day.equity,
                "gross_exposure": day.gross_exposure,
                "reasons": sorted(reasons),
                "decision_date": last.timestamp.date().isoformat() if last else None,
            }
        )
    return rows


def aggregate_exposure(rows: list[dict[str, Any]], recorder: Recorder) -> dict[str, Any]:
    n = len(rows)
    out: dict[str, Any] = {
        "total_days": n,
        "average_gross_exposure": statistics.fmean(r["gross_exposure"] for r in rows),
    }
    for reason in CASH_REASONS:
        active = [r for r in rows if reason in r["reasons"]]
        out[reason] = {
            "days_active": len(active),
            "pct_days": len(active) / n if n else 0.0,
            "average_gross_exposure_when_active": (
                statistics.fmean(r["gross_exposure"] for r in active) if active else None
            ),
        }
    # Daily-loss / drawdown incident counts from decisions.
    out["drawdown_latch_decisions"] = sum(
        1 for d in recorder.decisions if "portfolio_drawdown_halt" in d.overlay_reasons
    )
    out["daily_loss_latch_decisions"] = sum(
        1 for d in recorder.decisions if "daily_loss_limit" in d.overlay_reasons
    )
    out["skipped_decisions"] = len(recorder.skips)
    return out


# --------------------------------------------------------------------------
# Signal diagnostics (Phase 5)
# --------------------------------------------------------------------------


def spearman_ic(scores: list[float], fwd: list[float]) -> float:
    n = len(scores)
    if n < 3:
        return 0.0
    rank_s = _rank(scores)
    rank_f = _rank(fwd)
    mean_s = statistics.fmean(rank_s)
    mean_f = statistics.fmean(rank_f)
    cov = sum((a - mean_s) * (b - mean_f) for a, b in zip(rank_s, rank_f)) / (n - 1)
    var_s = sum((a - mean_s) ** 2 for a in rank_s) / (n - 1)
    var_f = sum((b - mean_f) ** 2 for b in rank_f) / (n - 1)
    if var_s <= 0 or var_f <= 0:
        return 0.0
    return cov / math.sqrt(var_s * var_f)


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    for pos, idx in enumerate(order):
        ranks[idx] = pos + 1
    return ranks


def build_price_index(
    bars: list[Any], *, market: Market, timeframe: str
) -> tuple[dict[date, dict[str, Any]], list[date]]:
    by_date: dict[date, dict[str, Any]] = {}
    for bar in bars:
        if bar.market != market or bar.timeframe != timeframe:
            continue
        by_date.setdefault(bar.timestamp.date(), {})[bar.symbol] = bar
    return by_date, sorted(by_date)


def cross_section_scores(
    config: Any,
    universe: Any,
    by_date: dict[date, dict[str, Any]],
    dates: list[date],
    t: date,
    strategy: PortfolioStrategy,
) -> dict[str, Any] | None:
    """V1 score cross-section at date t with forward returns (leak-free).

    Score uses closes strictly through t (momentum skips the decision bar
    by construction). Forward returns run from the next bar's OPEN to
    close[t+H], matching the engine's fill model (decide at close T, fill at
    T+1 open).
    """
    today = by_date.get(t)
    if today is None:
        return None
    t_idx = dates.index(t)
    scores: list[float] = []
    symbols: list[str] = []
    forward: list[dict[int, float]] = []
    trend_window = int(strategy.parameters.get("trend_window", 150))
    momentum_window = int(strategy.parameters.get("momentum_window", 60))
    momentum_skip = int(strategy.parameters.get("momentum_skip", 1))
    use_stock_trend = bool(strategy.parameters.get("use_stock_trend", True))
    for symbol, bar in sorted(today.items()):
        if symbol == "SPY":
            continue
        if not _in_universe(universe, symbol, t):
            continue
        closes = _closes_by_date(by_date, symbol, t, lookback=300)
        if closes is None or len(closes) < strategy.minimum_history_bars:
            continue
        score = momentum_at(closes, momentum_window, skip_bars=momentum_skip)
        trend = _sma(closes, trend_window)
        if not math.isfinite(score) or score <= 0:
            continue
        if use_stock_trend and (trend is None or closes[-1] <= trend):
            continue
        fwd: dict[int, float] = {}
        ok = True
        for h in HORIZONS:
            target = t_idx + h
            if target >= len(dates):
                ok = False
                break
            next_open = _price_of(by_date, dates[t_idx + 1], symbol, "open")
            target_close = _price_of(by_date, dates[target], symbol, "close")
            if next_open is None or target_close is None or next_open <= 0:
                ok = False
                break
            fwd[h] = target_close / next_open - 1.0
        if not ok or not fwd:
            continue
        scores.append(score)
        symbols.append(symbol)
        forward.append(fwd)
    if len(scores) < 3:
        return None
    out: dict[str, Any] = {"symbols": symbols, "scores": scores, "forward": forward}
    # SPY forward returns over the same horizons for relative excess.
    spy_fwd: dict[int, float] = {}
    for h in HORIZONS:
        target = t_idx + h
        if target < len(dates):
            next_open = _price_of(by_date, dates[t_idx + 1], "SPY", "open")
            target_close = _price_of(by_date, dates[target], "SPY", "close")
            if next_open and target_close and next_open > 0:
                spy_fwd[h] = target_close / next_open - 1.0
    # Regime labels from data known at t only.
    spy_closes = _closes_by_date(by_date, "SPY", t, lookback=252)
    spy_above_trend = False
    spy_vol = None
    if spy_closes and len(spy_closes) >= 200:
        trend = _sma(spy_closes, 200)
        spy_above_trend = trend is not None and spy_closes[-1] > trend
    if spy_closes and len(spy_closes) >= 64:
        rets = [
            spy_closes[i] / spy_closes[i - 1] - 1
            for i in range(len(spy_closes) - 63, len(spy_closes))
            if spy_closes[i - 1] > 0
        ]
        if len(rets) > 20:
            spy_vol = statistics.pstdev(rets) * math.sqrt(252)
    out["spy_above_trend"] = spy_above_trend
    out["spy_realized_vol"] = spy_vol
    for h in HORIZONS:
        fwd = [f[h] for f in forward]
        out[f"ic_{h}"] = spearman_ic(scores, fwd)
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        top = order[:6]
        top_mean = statistics.fmean(fwd[i] for i in top)
        uni_median = statistics.median(fwd)
        out[f"top6_excess_{h}"] = top_mean - uni_median
        out[f"top6_mean_{h}"] = top_mean
        out[f"universe_median_{h}"] = uni_median
        if h in spy_fwd:
            out[f"top6_excess_vs_spy_{h}"] = top_mean - spy_fwd[h]
            out[f"spy_fwd_{h}"] = spy_fwd[h]
    return out


def _in_universe(universe: Any, symbol: str, t: date) -> bool:

    ts = datetime.combine(t, datetime.min.time(), tzinfo=UTC)
    from parayabuck.markets.symbols import canonical_symbol

    try:
        return bool(universe.contains(canonical_symbol(symbol, Market.EQUITY), Market.EQUITY, ts))
    except Exception:
        return False


def _closes_by_date(
    by_date: dict[date, dict[str, Any]], symbol: str, t: date, *, lookback: int
) -> list[float] | None:
    closes: list[float] = []
    for d in sorted(d for d in by_date if d <= t):
        bar = by_date[d].get(symbol)
        if bar is None:
            continue
        closes.append(bar.close)
    if len(closes) < 2:
        return None
    return closes[-lookback:]


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return statistics.fmean(values[-window:])


def _price_of(
    by_date: dict[date, dict[str, Any]], d: date, symbol: str, field_name: str
) -> float | None:
    bar = by_date.get(d, {}).get(symbol)
    return getattr(bar, field_name) if bar is not None else None


def signal_diagnostics(
    config: Any,
    bars: list[Any],
    *,
    start: datetime,
    end: datetime,
    strategy_name: str,
    parameters: dict[str, object] | None,
    output_dir: Path,
) -> dict[str, Any]:
    market = Market.EQUITY
    strategy = portfolio_strategy_from(strategy_name, parameters)
    universe = build_product_universe(
        config, bars, market=market, end=end, timeframe=strategy.timeframe
    )
    by_date, dates = build_price_index(bars, market=market, timeframe=strategy.timeframe)
    dates = [d for d in dates if start.date() <= d <= (end.date() if end else d)]
    cross_sections: list[dict[str, Any]] = []
    for i, d in enumerate(dates):
        if i > 0 and dates[i - 1].isocalendar()[:2] == d.isocalendar()[:2]:
            continue  # weekly
        cs = cross_section_scores(config, universe, by_date, dates, d, strategy)
        if cs is not None:
            cs["date"] = d.isoformat()
            cross_sections.append(cs)
    summary: dict[str, Any] = {
        "rebalances": len(cross_sections),
        "horizons": {},
        "by_year": {},
        "by_regime": {},
    }
    for h in HORIZONS:
        ics = [cs[f"ic_{h}"] for cs in cross_sections]
        top_excess = [cs[f"top6_excess_{h}"] for cs in cross_sections]
        spy_excess = [
            cs[f"top6_excess_vs_spy_{h}"]
            for cs in cross_sections
            if f"top6_excess_vs_spy_{h}" in cs
        ]
        summary["horizons"][str(h)] = {
            "ic_mean": statistics.fmean(ics) if ics else None,
            "ic_hit_rate": sum(1 for x in ics if x > 0) / len(ics) if ics else None,
            "top6_excess_mean": statistics.fmean(top_excess) if top_excess else None,
            "top6_excess_vs_spy_mean": statistics.fmean(spy_excess) if spy_excess else None,
        }
    for year in sorted({int(cs["date"][:4]) for cs in cross_sections}):
        year_cs = [cs for cs in cross_sections if cs["date"].startswith(str(year))]
        year_row: dict[str, Any] = {"n": len(year_cs)}
        for h in HORIZONS:
            ics = [cs[f"ic_{h}"] for cs in year_cs]
            year_row[str(h)] = {
                "ic_mean": statistics.fmean(ics) if ics else None,
                "ic_hit_rate": sum(1 for x in ics if x > 0) / len(ics) if ics else None,
            }
        summary["by_year"][str(year)] = year_row

    # Regime segmentation: SPY trend (SMA200) and realized vol (63d), both
    # point-in-time at each decision.
    def _bucket(label: str, pred: Any) -> None:
        bucket = [cs for cs in cross_sections if pred(cs)]
        row: dict[str, Any] = {"n": len(bucket)}
        for h in HORIZONS:
            ics = [cs[f"ic_{h}"] for cs in bucket]
            row[str(h)] = {
                "ic_mean": statistics.fmean(ics) if ics else None,
                "ic_hit_rate": sum(1 for x in ics if x > 0) / len(ics) if ics else None,
            }
        summary["by_regime"][label] = row

    _bucket("spy_above_sma200", lambda cs: cs["spy_above_trend"] is True)
    _bucket("spy_below_sma200", lambda cs: cs["spy_above_trend"] is False)
    vols = [cs["spy_realized_vol"] for cs in cross_sections if cs.get("spy_realized_vol")]
    if vols:
        median_vol = statistics.median(vols)
        _bucket(
            "high_market_vol",
            lambda cs: (
                cs.get("spy_realized_vol") is not None and cs["spy_realized_vol"] > median_vol
            ),
        )
        _bucket(
            "low_market_vol",
            lambda cs: (
                cs.get("spy_realized_vol") is not None and cs["spy_realized_vol"] <= median_vol
            ),
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "signal.json").write_text(json.dumps(summary, indent=2, default=str))
    (output_dir / "cross_sections.json").write_text(
        json.dumps(cross_sections, indent=2, default=str)
    )
    return summary


# --------------------------------------------------------------------------
# Scanner attribution (Phase 7)
# --------------------------------------------------------------------------


def _median_dollar_volume(bars: list[Any]) -> float:
    values = [b.close * b.volume for b in bars if b.volume > 0]
    return statistics.median(values) if values else 0.0


def _atr_pct(bars: list[Any]) -> float:
    if len(bars) < 2:
        return 0.0
    price = bars[-1].close
    if price <= 0:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close
        tr = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - prev_close),
            abs(bars[i].low - prev_close),
        )
        trs.append(tr)
    return statistics.fmean(trs) / price if trs else 0.0


def stage_a_shortlist(
    by_date: dict[date, dict[str, Any]],
    dates: list[date],
    t: date,
    *,
    lookback_days: int = 60,
    target: int = 300,
    min_bars: int = 5,
) -> list[str]:
    """Reconstruct the Stage-A top-N shortlist at date t (strictly trailing)."""
    cutoff = t - timedelta(days=lookback_days)
    window: dict[str, list[Any]] = {}
    for d in sorted(by_date):
        if d >= t:
            break
        if d < cutoff:
            continue
        for symbol, bar in by_date[d].items():
            window.setdefault(symbol, []).append(bar)
    ranked = sorted(
        (
            (_median_dollar_volume(bars), symbol)
            for symbol, bars in window.items()
            if len(bars) >= min_bars
        ),
        key=lambda item: (-item[0], item[1]),
    )
    return [symbol for _dv, symbol in ranked[:target]]


def scanner_attribution(
    config: Any,
    bars: list[Any],
    *,
    start: datetime,
    end: datetime,
    strategy_name: str,
    parameters: dict[str, object] | None,
    output_dir: Path,
) -> dict[str, Any]:
    """Compare V1 signal quality on Stage-A (300) vs scanner-active (30)."""
    market = Market.EQUITY
    strategy = portfolio_strategy_from(strategy_name, parameters)
    universe = build_product_universe(
        config, bars, market=market, end=end, timeframe=strategy.timeframe
    )
    by_date, dates = build_price_index(bars, market=market, timeframe=strategy.timeframe)
    dates = [d for d in dates if start.date() <= d <= (end.date() if end else d)]
    trend_window = int(strategy.parameters.get("trend_window", 150))
    momentum_window = int(strategy.parameters.get("momentum_window", 60))
    weeks: list[dict[str, Any]] = []
    prev_stage_a: list[str] | None = None
    prev_active: list[str] | None = None
    for i, d in enumerate(dates):
        if i > 0 and dates[i - 1].isocalendar()[:2] == d.isocalendar()[:2]:
            continue
        stage_a = stage_a_shortlist(by_date, dates, d)
        active = [s for s in stage_a if _in_universe(universe, s, d)]

        # V1 scores on each universe.
        def score_cross(symbols: list[str]) -> dict[str, Any]:
            scores: list[float] = []
            fwd5: list[float] = []
            for symbol in symbols:
                if symbol == "SPY":
                    continue
                closes = _closes_by_date(by_date, symbol, d, lookback=252)
                if closes is None or len(closes) < strategy.minimum_history_bars:
                    continue
                score = momentum_at(closes, momentum_window, skip_bars=1)
                trend = _sma(closes, trend_window)
                if not math.isfinite(score) or score <= 0 or trend is None or closes[-1] <= trend:
                    continue
                target = i + 21
                if target >= len(dates):
                    continue
                next_open = _price_of(by_date, dates[i + 1], symbol, "open")
                target_close = _price_of(by_date, dates[target], symbol, "close")
                if next_open is None or target_close is None or next_open <= 0:
                    continue
                scores.append(score)
                fwd5.append(target_close / next_open - 1.0)
            return {"scores": scores, "fwd21": fwd5}

        cs_a = score_cross(stage_a)
        cs_b = score_cross(active)
        churn_a = (
            len(set(stage_a) - set(prev_stage_a)) / len(stage_a)
            if prev_stage_a is not None
            else None
        )
        churn_b = (
            len(set(active) - set(prev_active)) / len(active) if prev_active is not None else None
        )
        # Top-decile momentum names on Stage-A excluded from scanner-active.
        if len(cs_a["scores"]) >= 10:
            order = sorted(range(len(cs_a["scores"])), key=lambda k: -cs_a["scores"][k])
            top_decile = [stage_a[k] for k in order[: max(1, len(order) // 10)]]
            excluded = [s for s in top_decile if s not in set(active)]
        else:
            excluded = []
        weeks.append(
            {
                "date": d.isoformat(),
                "stage_a_size": len(stage_a),
                "active_size": len(active),
                "stage_a_scoreable": len(cs_a["scores"]),
                "active_scoreable": len(cs_b["scores"]),
                "ic_stage_a": (
                    spearman_ic(cs_a["scores"], cs_a["fwd21"]) if len(cs_a["scores"]) >= 3 else None
                ),
                "ic_active": (
                    spearman_ic(cs_b["scores"], cs_b["fwd21"]) if len(cs_b["scores"]) >= 3 else None
                ),
                "churn_stage_a": churn_a,
                "churn_active": churn_b,
                "top_decile_excluded": excluded,
            }
        )
        prev_stage_a = stage_a
        prev_active = active
    valid = [w for w in weeks if w["ic_stage_a"] is not None and w["ic_active"] is not None]
    summary: dict[str, Any] = {
        "weeks": len(weeks),
        "mean_ic_stage_a": (statistics.fmean(w["ic_stage_a"] for w in valid) if valid else None),
        "mean_ic_active": (statistics.fmean(w["ic_active"] for w in valid) if valid else None),
        "mean_churn_stage_a": statistics.fmean(
            w["churn_stage_a"] for w in weeks if w["churn_stage_a"] is not None
        ),
        "mean_churn_active": statistics.fmean(
            w["churn_active"] for w in weeks if w["churn_active"] is not None
        ),
        "median_active_size": statistics.median(w["active_size"] for w in weeks),
        "top_decile_exclusions_per_week": statistics.fmean(
            len(w["top_decile_excluded"]) for w in weeks
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "scanner_attribution.json").write_text(
        json.dumps({"summary": summary, "weeks": weeks}, indent=2, default=str)
    )
    return summary


# --------------------------------------------------------------------------
# Benchmarks (Phase 8)
# --------------------------------------------------------------------------


def benchmark_returns(bars: list[Any], *, start: datetime, end: datetime) -> dict[str, Any]:
    """SPY buy-hold, 50% SPY/cash, risk-matched SPY from the same bar set."""
    spy = sorted(
        (b for b in bars if b.symbol == "SPY" and start <= b.timestamp <= end),
        key=lambda b: b.timestamp,
    )
    if len(spy) < 2:
        return {}
    closes = [b.close for b in spy]
    n = len(closes)
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, n)]
    spy_total = closes[-1] / closes[0] - 1
    half_total = 0.5 * spy_total
    # Vol-targeted SPY: scale daily SPY return to a 10% annualized vol budget
    # using a trailing 63-day realized vol, rebalanced daily at close.
    scale = [1.0]
    for i in range(1, n):
        window = rets[max(0, i - 63) : i]
        vol = statistics.pstdev(window) * math.sqrt(252) if len(window) > 5 else 0.20
        scale.append(min(2.0, 0.10 / vol) if vol > 0 else 0.0)
    vt_rets = [rets[i] * scale[i] for i in range(len(rets))]
    vt_total = 1.0
    for r in vt_rets:
        vt_total *= 1.0 + r
    vt_total -= 1.0
    sharpe = _sharpe(rets)
    vt_sharpe = _sharpe(vt_rets)
    return {
        "spy_total_return": spy_total,
        "spy_sharpe": sharpe,
        "spy_max_drawdown": _max_drawdown(closes),
        "half_spy_cash_total_return": half_total,
        "half_spy_cash_sharpe": _sharpe([0.5 * r for r in rets]),
        "vol_targeted_spy_total_return": vt_total,
        "vol_targeted_spy_sharpe": vt_sharpe,
        "vol_targeted_spy_max_drawdown": _max_drawdown_from_rets(vt_rets),
        "vol_targeted_spy_avg_scale": statistics.fmean(scale),
    }


def _sharpe(daily_rets: list[float]) -> float:
    if len(daily_rets) < 3:
        return 0.0
    mean = statistics.fmean(daily_rets)
    std = statistics.pstdev(daily_rets)
    if std <= 0:
        return 0.0
    return mean / std * math.sqrt(252)


def _max_drawdown(closes: list[float]) -> float:
    peak = closes[0]
    mdd = 0.0
    for c in closes:
        peak = max(peak, c)
        mdd = max(mdd, (peak - c) / peak)
    return mdd


def _max_drawdown_from_rets(rets: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    mdd = 0.0
    for r in rets:
        equity *= 1.0 + r
        peak = max(peak, equity)
        mdd = max(mdd, (peak - equity) / peak)
    return mdd


def pnl_attribution(recorder: Recorder, result: Any) -> dict[str, Any]:
    """Build a trade blotter from fills and attribute PnL (Phase 6)."""
    decision_by_ts = {d.timestamp: d for d in recorder.decisions}

    def decision_for(fill_ts: datetime, decision_ts: datetime) -> DecisionRecord | None:
        return decision_by_ts.get(decision_ts)

    # FIFO trade construction per symbol.
    trades: list[dict[str, Any]] = []
    open_trades: dict[str, list[dict[str, Any]]] = {}

    def close_trade(symbol: str, sell: dict[str, Any]) -> None:
        queue = open_trades.get(symbol, [])
        remaining = sell["qty"]
        proceeds = 0.0
        cost = 0.0
        fees = 0.0
        entry_ts = None
        while remaining > 1e-12 and queue:
            lot = queue[0]
            take = min(remaining, lot["qty"])
            proceeds += take * sell["fill_price"]
            cost += take * lot["fill_price"]
            fees += take / lot["qty"] * lot["fee"] + take / sell["qty"] * sell["fee"]
            entry_ts = lot["timestamp"]
            lot["qty"] -= take
            remaining -= take
            if lot["qty"] <= 1e-12:
                queue.pop(0)
        if entry_ts is None:
            return
        pnl = proceeds - cost - fees
        decision = decision_for(sell["timestamp"], sell["decision_timestamp"])
        trades.append(
            {
                "symbol": symbol,
                "entry_ts": entry_ts.isoformat(),
                "exit_ts": sell["timestamp"].isoformat(),
                "holding_days": (sell["timestamp"].date() - entry_ts.date()).days,
                "pnl": pnl,
                "exit_reason": (
                    "end_of_run_liquidation"
                    if decision is None
                    else ("intermediate_risk" if decision.intermediate_risk else "scheduled")
                ),
            }
        )

    for fill in result.fills:
        row = {
            "timestamp": fill.timestamp,
            "symbol": fill.symbol,
            "side": fill.side.value,
            "qty": fill.qty,
            "fill_price": fill.fill_price,
            "fee": fill.fee,
            "decision_timestamp": fill.decision_timestamp,
        }
        if row["side"] == "buy":
            open_trades.setdefault(row["symbol"], []).append(row)
        else:
            close_trade(row["symbol"], row)
    total_pnl = sum(t["pnl"] for t in trades)
    by_symbol: dict[str, float] = {}
    for t in trades:
        by_symbol[t["symbol"]] = by_symbol.get(t["symbol"], 0.0) + t["pnl"]
    top = sorted(by_symbol.items(), key=lambda kv: -kv[1])
    worst = sorted(by_symbol.items(), key=lambda kv: kv[1])
    # Monthly PnL concentration from the equity curve.
    daily_pnl = [
        result.equity_curve[i] - result.equity_curve[i - 1]
        for i in range(1, len(result.equity_curve))
    ]
    monthly: dict[str, float] = {}
    for i in range(1, len(result.equity_curve)):
        key = result.curve_timestamps[i].strftime("%Y-%m")
        monthly[key] = monthly.get(key, 0.0) + daily_pnl[i - 1]
    best_month = max(monthly.items(), key=lambda kv: kv[1]) if monthly else ("", 0.0)
    by_year: dict[str, float] = {}
    for key, value in monthly.items():
        year = key[:4]
        by_year[year] = by_year.get(year, 0.0) + value
    best_year = max(by_year.items(), key=lambda kv: kv[1]) if by_year else ("", 0.0)
    return {
        "total_realized_pnl": total_pnl,
        "trade_count": len(trades),
        "win_rate": sum(1 for t in trades if t["pnl"] > 0) / len(trades) if trades else None,
        "largest_winning_trade": max((t["pnl"] for t in trades), default=0.0),
        "largest_losing_trade": min((t["pnl"] for t in trades), default=0.0),
        "top5_contributors": [(s, round(v, 2)) for s, v in top[:5]],
        "worst5_contributors": [(s, round(v, 2)) for s, v in worst[:5]],
        "best_month_pct_of_pnl": (best_month[1] / max(abs(total_pnl), 1e-9) if total_pnl else None),
        "best_month": best_month[0],
        "best_year_pct_of_pnl": best_year[1] / max(abs(total_pnl), 1e-9) if total_pnl else None,
        "best_year": best_year[0],
        "trades_by_exit_reason": {
            reason: sum(1 for t in trades if t["exit_reason"] == reason)
            for reason in ("scheduled", "intermediate_risk", "end_of_run_liquidation")
        },
        "holding_days_median": (
            statistics.median(t["holding_days"] for t in trades) if trades else None
        ),
    }


# --------------------------------------------------------------------------
# Incidents (Phase 3)
# --------------------------------------------------------------------------


def incident_log(recorder: Recorder) -> dict[str, Any]:
    incidents = []
    for d in recorder.decisions:
        if not d.overlay_halted:
            continue
        incidents.append(
            {
                "timestamp": d.timestamp.isoformat(),
                "type": ",".join(d.overlay_reasons),
                "equity": d.equity_at_decision,
                "peak_equity": d.peak_equity,
                "drawdown": d.drawdown_pct,
                "daily_pnl_pct": d.daily_loss_pct,
                "positions": list(d.positions),
                "strategy_target_cash": d.target_cash_weight_pre_overlay,
            }
        )
    return {
        "incident_count": len(incidents),
        "incidents": incidents,
        "scheduled_skip_count": sum(1 for s in recorder.skips if s.get("scheduled")),
        "skips": recorder.skips,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _date(value: str) -> datetime:

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ParayaBuck strategy research harness")
    parser.add_argument("--config", default=str(REPO / "config" / "settings.yaml"))
    parser.add_argument(
        "--mode",
        choices=(
            "baseline",
            "alpha",
            "incidents",
            "exposure",
            "signal",
            "scanner",
            "benchmarks",
            "pnl",
            "all",
        ),
        default="all",
    )
    parser.add_argument(
        "--data",
        default=str(REPO / "data" / "historical" / "equity_1200_2019_2026.csv"),
    )
    parser.add_argument("--market", choices=("equity", "crypto"), default="equity")
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--start", default="2020-01-02")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--initial-cash", type=float, default=None)
    parser.add_argument("--cost-multiplier", type=float, default=1.0)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    market = Market(args.market)
    start = _date(args.start)
    end = _date(args.end) + timedelta(hours=23) if args.end else None
    bars = load_csv_bars(args.data, default_market=market)
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    strategy_name = args.strategy or selection.name
    # Non-default strategies use their own parameter defaults (or explicit
    # --param overrides); only the configured selection inherits config params.
    parameters: dict[str, Any] = {} if args.strategy else dict(selection.parameters)
    for item in args.param:
        name, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"--param expects NAME=VALUE, got {item!r}")
        if value.lower() in {"true", "false"}:
            parameters[name] = value.lower() == "true"
        else:
            try:
                parameters[name] = (
                    float(value) if "." in value or "e" in value.lower() else int(value)
                )
            except ValueError:
                parameters[name] = value
    output_dir = Path(args.output_dir) if args.output_dir else OUTPUT_ROOT
    modes = (
        ["baseline", "alpha", "incidents", "exposure", "signal", "scanner", "benchmarks", "pnl"]
        if args.mode == "all"
        else [args.mode]
    )
    for mode in modes:
        run_mode(
            mode,
            config,
            bars,
            market=market,
            strategy_name=strategy_name,
            parameters=parameters,
            start=start,
            end=end,
            initial_cash=args.initial_cash,
            cost_multiplier=args.cost_multiplier,
            output_dir=output_dir / mode,
        )
    return 0


def run_mode(
    mode: str,
    config: Any,
    bars: list[Any],
    *,
    market: Market,
    strategy_name: str,
    parameters: dict[str, object],
    start: datetime,
    end: datetime | None,
    initial_cash: float | None,
    cost_multiplier: float,
    output_dir: Path,
) -> None:
    if mode == "benchmarks":
        bm = benchmark_returns(bars, start=start, end=end or start + timedelta(days=4000))
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "benchmarks.json").write_text(json.dumps(bm, indent=2, default=str))
        print(json.dumps({"benchmarks": bm}, indent=2, default=str))
        return
    if mode == "scanner":
        sc = scanner_attribution(
            config,
            bars,
            start=start,
            end=end,
            strategy_name=strategy_name,
            parameters=parameters,
            output_dir=output_dir,
        )
        print(json.dumps({"scanner": sc}, indent=2, default=str))
        return
    # incidents/exposure need the PRODUCTION policy so real breaker trips are
    # observable; only 'alpha' runs the NON-DEPLOYABLE latch-free counterfactual.
    # 'pnl' attributes the alpha path (the meaningful trade set).
    disable = mode in ("alpha", "pnl")
    summary, recorder, _result = run_diagnostic(
        config,
        bars,
        market=market,
        strategy_name=strategy_name,
        parameters=parameters,
        start=start,
        end=end,
        initial_cash=initial_cash,
        cost_multiplier=cost_multiplier,
        disable_operational_latches=disable,
        output_dir=output_dir,
    )
    payload: dict[str, Any] = {mode: summary}
    if mode == "pnl":
        payload["pnl"] = pnl_attribution(recorder, _result)
        (output_dir / "pnl.json").write_text(json.dumps(payload["pnl"], indent=2, default=str))
    elif mode == "incidents":
        payload["incidents"] = incident_log(recorder)
    elif mode == "exposure":
        rows = decompose_exposure(recorder)
        payload["exposure_attribution"] = aggregate_exposure(rows, recorder)
        (output_dir / "exposure_daily.csv").write_text(
            "\n".join(
                [",".join(["date", "equity", "gross_exposure", "reasons"])]
                + [
                    ",".join(
                        [
                            r["date"],
                            f"{r['equity']:.2f}",
                            f"{r['gross_exposure']:.6f}",
                            "|".join(r["reasons"]),
                        ]
                    )
                    for r in rows
                ]
            )
        )
    elif mode == "signal":
        payload["signal"] = signal_diagnostics(
            config,
            bars,
            start=start,
            end=end,
            strategy_name=strategy_name,
            parameters=parameters,
            output_dir=output_dir,
        )
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
