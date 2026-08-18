#!/usr/bin/env python3
# ruff: noqa: E501  # research script: long report/print lines
"""Cross-candidate robustness audit for the cadence bake-off (research only).

Independent verification of the EQUITY (monthly vs weekly) and CRYPTO (168h vs
96h vs 72h) cadence candidates through the SAME production harness used by
paper/live (parayabuck.backtest.product), mirroring the frozen-candidate methodology of the cadence bake-off
(scripts/research_equity_cadence.py / research_crypto_cadence.py).

Sections:
  1. baseline reproduction of the documented controls (docs/strategy_robustness.md
     equity base 1x; docs/crypto_bakeoff_validation.md candidate B),
  2. methodology audit (equal windows / warmup equivalence / cost parity /
     universe parity / no future data / equal horizons),
  3. cost stress 1x/2x/3x per candidate,
  4. walk-forward (identical fold sets per market, shared universe object),
  5. concentration (top-1/2/3 contributions, equity jackknife, crypto
     pinned-symbol degeneracy),
  6. calendar-year returns per candidate,
  verdict: independent Pareto per market.

NEVER edits src/ or tests/. Outputs JSON under
data/research/cadence_research_2026_08/robustness/ and prints markdown tables.

Usage:
  python scripts/research_robustness_audit.py [--only baseline,cost,walkforward,concentration,calendar,methodology,verdict] [--markets equity,crypto]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from parayabuck.backtest.product import (
    ProductBacktestResult,
    build_product_universe,
    load_csv_bars,
    run_product_backtest,
)
from parayabuck.backtest.universe import (
    PointInTimeUniverse,
    hash_point_in_time_universe,
)
from parayabuck.config import Config, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data.aggregator import aggregate_historical_bars

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "data" / "research" / "cadence_research_2026_08" / "robustness"
PEER_EQUITY_DIR = REPO / "data" / "research" / "cadence_research_2026_08" / "equity"
PEER_CRYPTO_DIR = REPO / "data" / "research" / "cadence_research_2026_08" / "crypto"

EQUITY_CSV = REPO / "data" / "historical" / "equity_1200_2016_2026.csv"
CRYPTO_CSV_EXTENDED = REPO / "data" / "historical" / "crypto_18_1h_extended.csv"

# Canonical evaluation windows (match the prior validated research exactly).
EQUITY_START = datetime(2017, 1, 3, tzinfo=UTC)
EQUITY_END = datetime(2026, 7, 30, 23, 59, 59, tzinfo=UTC)
# Frozen reversal evaluation window (warmup 2021-01-01 .. 2021-03-31).
REVERSAL_EVAL_START = datetime(2021, 4, 1, tzinfo=UTC)
CRYPTO_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)

# Frozen production parameter sets. ONLY cadence is overridden per candidate.
FROZEN_EQUITY = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
}
FROZEN_CRYPTO = {
    "variance_lookback_days": 22,
    "top_n": 2,
    "rebalance_hours": 168,
}

# Documented control numbers (baseline reproduction targets).
EXPECTED_BASELINES = {
    "equity_monthly": {
        "return": 0.5921,
        "cagr": 0.0498,
        "sharpe": 0.47,
        "sortino": 0.60,
        "max_drawdown": 0.2545,
        "rebalance_count": 113,
        "trade_count": 300,
        "source": "docs/strategy_robustness.md Base 1x (full window)",
    },
    "crypto_168h": {
        "return": 0.0461,
        "cagr": 0.0085,
        "sharpe": 0.172,
        "sortino": 0.231,
        "max_drawdown": 0.1317,
        "profit_factor": 1.109,
        "trade_count": 124,
        "rebalance_count": 263,
        "turnover": 13.28,
        "turnover_annualized": 2.49,
        "fees_total": 3757.0,
        "source": "docs/crypto_bakeoff_validation.md candidate B",
    },
}


@dataclass(frozen=True)
class CandidateSpec:
    key: str
    strategy_name: str
    parameters: dict[str, object]
    strategy_timeframe: str
    control: bool


CANDIDATES: dict[Market, tuple[CandidateSpec, ...]] = {
    Market.EQUITY: (
        CandidateSpec(
            "equity_monthly", "equity_residual_momentum_v1", dict(FROZEN_EQUITY), "1d", True
        ),
        CandidateSpec(
            "equity_weekly",
            "equity_residual_momentum_v1",
            {**FROZEN_EQUITY, "rebalance_frequency": "weekly"},
            "1d",
            False,
        ),
    ),
    Market.CRYPTO: (
        CandidateSpec("crypto_168h", "crypto_low_variance_v1", dict(FROZEN_CRYPTO), "1d", True),
        CandidateSpec(
            "crypto_96h",
            "crypto_low_variance_v1",
            {**FROZEN_CRYPTO, "rebalance_hours": 96},
            "1d",
            False,
        ),
        CandidateSpec(
            "crypto_72h",
            "crypto_low_variance_v1",
            {**FROZEN_CRYPTO, "rebalance_hours": 72},
            "1d",
            False,
        ),
    ),
}

WINDOWS: dict[Market, tuple[datetime, datetime]] = {
    Market.EQUITY: (EQUITY_START, EQUITY_END),
    Market.CRYPTO: (REVERSAL_EVAL_START, CRYPTO_END),
}

DATASETS: dict[Market, tuple[Path, str]] = {
    Market.EQUITY: (EQUITY_CSV, "equity_1200_2016_2026.csv"),
    Market.CRYPTO: (CRYPTO_CSV_EXTENDED, "crypto_18_1h_extended.csv -> 1d aggregate"),
}


from scripts.research_helpers import _calendar_fold_window, _fold_window  # noqa: E402


def calendar_monthrange(year: int, month: int) -> int:
    import calendar

    return calendar.monthrange(year, month)[1]


def _params_match(candidate: dict[str, object], frozen: dict[str, object]) -> bool:
    """True when the candidate's parameter dict agrees with the frozen set on
    every frozen key (tolerant of extra keys such as our cadence override)."""
    for key, value in frozen.items():
        if key not in candidate:
            return False
        if isinstance(value, (list, tuple)):
            if list(candidate[key]) != list(value):
                return False
        elif str(candidate[key]) != str(value):
            return False
    return True


def _capture_run(node: dict[str, object], path: str, matches: list[dict[str, object]]) -> None:
    """Record a peer run dict carrying a cost multiplier and a return value."""
    for key in ("1x", "2x", "3x", "cost_1x", "cost_2x", "cost_3x"):
        if key in node and isinstance(node[key], dict):
            sub = node[key]
            ret = (
                sub.get("return")
                if isinstance(sub.get("return"), (int, float))
                else sub.get("total_return")
            )
            if isinstance(ret, (int, float)):
                matches.append(
                    {
                        "file": path.split(".")[0],
                        "multiplier": key
                        if not key.startswith("cost_")
                        else key.replace("cost_", "") + "x",
                        "peer_return": round(float(ret), 6),
                    }
                )
    ret = node.get("return") or node.get("total_return")
    mult = node.get("cost_multiplier") or node.get("multiplier")
    if isinstance(ret, (int, float)) and mult is not None:
        matches.append(
            {
                "file": path.split(".")[0],
                "multiplier": f"{float(mult):g}x",
                "peer_return": round(float(ret), 6),
            }
        )


def _md_table(headers: list[str], rows: list[list[object]]) -> str:
    def fmt(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.4f}"
        return str(value)

    out = ["| " + " | ".join(headers) + " |"]
    out.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        out.append("| " + " | ".join(fmt(v) for v in row) + " |")
    return "\n".join(out)


def _profit_factor(result) -> float:
    gross_profit = sum(pnl for pnl in result.realized_pnls if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in result.realized_pnls if pnl < 0))
    if gross_loss > 0:
        return gross_profit / gross_loss
    return float("inf") if gross_profit > 0 else 0.0


def _avg_equity(result) -> float:
    curve = result.equity_curve
    return sum(curve) / len(curve) if curve else result.starting_equity


def _run_record(run: ProductBacktestResult, *, cost_multiplier: float) -> dict[str, object]:
    """Compact, deterministic record of one production-harness run."""
    result = run.result
    summary = run.report.summary
    total_cost = result.slippage_cost_total + result.spread_cost_total + result.fee_total
    gross_pnl = result.starting_equity * result.gross_return
    avg_equity = _avg_equity(result)
    return {
        "window": [result.start_date, result.end_date],
        "duration_days": result.duration_days,
        "cost_multiplier": cost_multiplier,
        "summary_cost_multiplier": summary.get("cost_multiplier"),
        "universe_hash": summary.get("universe_hash"),
        "universe_methodology": summary.get("universe_methodology"),
        "strategy_parameters": summary.get("strategy_parameters"),
        "return": round(result.total_return, 6),
        "gross_return": round(result.gross_return, 6),
        "net_return": round(result.net_return, 6),
        "cagr": round(result.cagr, 6),
        "sharpe": round(result.sharpe, 6),
        "sortino": round(result.sortino, 6),
        "calmar": round(result.calmar, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "profit_factor": round(_profit_factor(result), 4),
        "turnover": round(result.turnover, 6),
        "slippage_cost_total": round(result.slippage_cost_total, 2),
        "spread_cost_total": round(result.spread_cost_total, 2),
        "fee_total": round(result.fee_total, 2),
        "total_cost": round(total_cost, 2),
        "fees_pct_of_avg_equity": round(total_cost / avg_equity, 6) if avg_equity > 0 else 0.0,
        "fees_pct_of_gross_pnl": (
            round(total_cost / abs(gross_pnl), 6) if abs(gross_pnl) > 1e-9 else None
        ),
        "cost_per_unit_turnover": (
            round(total_cost / result.turnover, 6) if result.turnover > 0 else 0.0
        ),
        "avg_holdings": round(result.average_holdings_count, 3),
        "avg_cash_ratio": round(result.average_cash_ratio, 4),
        "avg_exposure": round(1.0 - result.average_cash_ratio, 4),
        "rebalance_count": result.rebalance_count,
        "trade_count": len(result.closed_position_events),
        "maximum_concentration": round(result.maximum_concentration, 4),
        "benchmark_return": round(result.benchmark_return, 6),
        "benchmark_symbol": result.benchmark_symbol,
        "worst_year_return": round(result.worst_year_return, 6),
        "positive_month_ratio": round(result.positive_month_ratio, 4),
    }


class AuditRunner:
    """Runs all audit sections against shared in-memory data (universes built
    ONCE per market, reused across every candidate and every cost multiplier —
    the mission requirement)."""

    def __init__(self, config: Config, markets: set[Market]) -> None:
        self.config = config
        self.markets = markets
        self.equity_bars: list[Bar] | None = None
        self.crypto_bars_1h: list[Bar] | None = None
        self.crypto_bars_1d: list[Bar] | None = None
        self.universes: dict[Market, PointInTimeUniverse] = {}
        self.cache: dict[tuple, dict[str, object]] = {}

    # ------------------------------------------------------------------ data
    def _load_data(self, market: Market) -> None:
        if market == Market.EQUITY:
            if self.equity_bars is None:
                print(f"Loading equity dataset ({EQUITY_CSV.name})...", flush=True)
                self.equity_bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
                print(f"  {len(self.equity_bars):,} bars", flush=True)
        else:
            if self.crypto_bars_1h is None:
                print(
                    f"Loading extended crypto dataset ({CRYPTO_CSV_EXTENDED.name})...", flush=True
                )
                self.crypto_bars_1h = load_csv_bars(
                    CRYPTO_CSV_EXTENDED, default_market=Market.CRYPTO
                )
                print(f"  {len(self.crypto_bars_1h):,} 1h bars", flush=True)
                self.crypto_bars_1d = aggregate_historical_bars(
                    self.crypto_bars_1h, "1d", require_complete=True
                )
                print(f"  aggregated {len(self.crypto_bars_1d):,} 1d bars", flush=True)

    def _universe(self, market: Market) -> PointInTimeUniverse:
        if market not in self.universes:
            self._load_data(market)
            if market == Market.EQUITY:
                print(
                    "Building point-in-time equity universe (production scanner policy)...",
                    flush=True,
                )
                universe = build_product_universe(
                    self.config, self.equity_bars, market=Market.EQUITY, end=EQUITY_END
                )
            else:
                print(
                    "Building point-in-time crypto universe from 1h scanner bars (timeframe='1h')...",
                    flush=True,
                )
                universe = build_product_universe(
                    self.config,
                    self.crypto_bars_1h,
                    market=Market.CRYPTO,
                    end=CRYPTO_END,
                    timeframe="1h",
                )
            snapshots = universe.snapshots
            candidate_counts = [snapshot.candidate_count for snapshot in snapshots]
            print(
                f"  snapshots={len(snapshots)} candidates avg={statistics.fmean(candidate_counts):.1f} "
                f"hash={hash_point_in_time_universe(universe)[:16]}...",
                flush=True,
            )
            self.universes[market] = universe
        return self.universes[market]

    def _bars(self, market: Market) -> list[Bar]:
        self._load_data(market)
        return self.equity_bars if market == Market.EQUITY else self.crypto_bars_1d

    # ------------------------------------------------------------------ runs
    def run(
        self,
        market: Market,
        spec: CandidateSpec,
        *,
        cost_multiplier: float,
        start: datetime,
        end: datetime,
        universe: PointInTimeUniverse,
    ) -> dict[str, object]:
        key = (
            market,
            spec.key,
            cost_multiplier,
            start.isoformat(),
            end.isoformat(),
            hash_point_in_time_universe(universe),
        )
        if key in self.cache:
            return self.cache[key]
        bars = self._bars(market)
        run = run_product_backtest(
            self.config,
            bars,
            market=market,
            strategy_name=spec.strategy_name,
            parameters=spec.parameters,
            strategy_timeframe=spec.strategy_timeframe,
            start=start,
            end=end,
            cost_multiplier=cost_multiplier,
            persist=False,
            persist_artifacts=False,
            universe=universe,
        )
        record = _run_record(run, cost_multiplier=cost_multiplier)
        record["_run"] = run  # transient handle for fold/concentration analysis
        self.cache[key] = record
        print(
            f"  [{market.value}] {spec.key} {cost_multiplier:g}x "
            f"{start.date()}..{end.date()} return={record['return']:.4%} "
            f"sharpe={record['sharpe']:.3f} rebal={record['rebalance_count']} trades={record['trade_count']}",
            flush=True,
        )
        return record

    def cost_stress_runs(self, market: Market, spec: CandidateSpec) -> dict[str, dict[str, object]]:
        start, end = WINDOWS[market]
        universe = self._universe(market)
        return {
            f"{m:g}x": self.run(
                market, spec, cost_multiplier=m, start=start, end=end, universe=universe
            )
            for m in (1.0, 2.0, 3.0)
        }

    def full_1x_run(self, market: Market, spec: CandidateSpec) -> dict[str, object]:
        start, end = WINDOWS[market]
        return self.run(
            market, spec, cost_multiplier=1.0, start=start, end=end, universe=self._universe(market)
        )

    # ------------------------------------------------------------- section 1
    def section_baseline(self) -> dict[str, object]:
        print("\n# SECTION 1 — BASELINE REPRODUCTION (control vs documented numbers)", flush=True)
        payload: dict[str, object] = {}
        for market in (Market.EQUITY, Market.CRYPTO):
            if market not in self.markets:
                continue
            for spec in CANDIDATES[market]:
                if not spec.control:
                    continue
                record = self.full_1x_run(market, spec)
                expected = EXPECTED_BASELINES[spec.key]
                checks: dict[str, object] = {}
                for metric, doc_value in expected.items():
                    if metric in ("source",):
                        continue
                    if metric == "turnover_annualized":
                        actual = record["turnover"] / (record["duration_days"] / 365.25)
                    elif metric == "fees_total":
                        actual = record["total_cost"]
                    elif metric == "profit_factor":
                        actual = record["profit_factor"]
                    elif metric == "trade_count":
                        actual = record["trade_count"]
                    elif metric == "rebalance_count":
                        actual = record["rebalance_count"]
                    else:
                        actual = record[metric]
                    rel = abs(actual - doc_value) / abs(doc_value) if doc_value else 0.0
                    abs_diff = abs(actual - doc_value)
                    exceeded = rel > 0.01 if metric != "sharpe" else abs_diff > 0.02
                    if metric == "sharpe":
                        flag = ">0.02 abs" if abs_diff > 0.02 else "<=0.02 abs"
                    else:
                        flag = ">1% relative" if rel > 0.01 else "<=1% relative"
                    checks[metric] = {
                        "documented": doc_value,
                        "reproduced": round(float(actual), 6),
                        "relative_drift": round(rel, 6),
                        "abs_difference": round(abs_diff, 6),
                        "drift_exceeded": bool(exceeded),
                        "flag": flag,
                    }
                violations = {k: v for k, v in checks.items() if v["drift_exceeded"]}
                payload[spec.key] = {
                    "window": record["window"],
                    "universe_hash": record["universe_hash"],
                    "parameters": record["strategy_parameters"],
                    "checks": checks,
                    "reproduction_match": len(violations) == 0,
                    "violations": sorted(violations),
                    "documented_source": expected["source"],
                }
                rows = [
                    [k, v["documented"], v["reproduced"], v["relative_drift"], v["flag"]]
                    for k, v in checks.items()
                ]
                print(f"\nBaseline: {spec.key} vs {expected['source']}", flush=True)
                print(
                    _md_table(
                        ["metric", "documented", "reproduced", "relative drift", "flag"],
                        rows,
                    ),
                    flush=True,
                )
                print(
                    f"  reproduction_match={len(violations) == 0}"
                    + (f"  VIOLATIONS: {sorted(violations)}" if violations else ""),
                    flush=True,
                )
        return payload

    # ------------------------------------------------------------- section 2
    @staticmethod
    def _t1_check(record: dict[str, object]) -> dict[str, object]:
        run: ProductBacktestResult = record["_run"]
        result = run.result
        decisions = sorted(record["_run"].result.rebalances, key=lambda r: r.decision_timestamp)
        decision_ts = [d.decision_timestamp for d in decisions]
        fill_ts = sorted(f.timestamp for f in result.fills)
        # Authoritative T+1 check: a fill is only legal strictly after the
        # decision that created it (PortfolioFill.decision_timestamp). The
        # engine fills orders on bars strictly after creation and _fill_cycle
        # runs BEFORE _decide at each timestamp, so a same-timestamp
        # decision+fill pair would be an actual violation.
        violations = sum(1 for f in result.fills if f.timestamp <= f.decision_timestamp)
        gaps = [
            (f.timestamp - f.decision_timestamp).total_seconds() / 3600.0
            for f in result.fills
            if f.timestamp > f.decision_timestamp
        ]
        min_gap_hours = min(gaps) if gaps else None
        return {
            "decision_count": len(decision_ts),
            "fill_count": len(fill_ts),
            "first_decision": decision_ts[0].isoformat() if decision_ts else None,
            "last_decision": decision_ts[-1].isoformat() if decision_ts else None,
            "first_fill": fill_ts[0].isoformat() if fill_ts else None,
            "last_fill": fill_ts[-1].isoformat() if fill_ts else None,
            "window": record["window"],
            "min_fill_after_decision_hours": round(min_gap_hours, 3)
            if min_gap_hours is not None
            else None,
            "same_bar_fill_violations": violations,
            "fill_model": "decision on closed bar; fill at next tradable bar open",
        }

    def section_methodology(self, all_records: dict[str, object]) -> dict[str, object]:
        print("\n# SECTION 2 — METHODOLOGY AUDIT (falsification attempts)", flush=True)
        checks: dict[str, object] = {}
        for market in (Market.EQUITY, Market.CRYPTO):
            if market not in self.markets:
                continue
            mkey = market.value
            specs = CANDIDATES[market]
            universe = self._universe(market)
            runs_1x = {spec.key: all_records[mkey][spec.key]["1x"] for spec in specs}

            # (a) equal windows across candidates
            windows = {k: (r["window"][0], r["window"][1]) for k, r in runs_1x.items()}
            equal_windows = len(set(windows.values())) == 1
            # (b) warmup equivalence: same data + same minimum_history_bars logic
            from parayabuck.portfolio import portfolio_strategy_from

            min_hist = {}
            for spec in specs:
                strat = portfolio_strategy_from(
                    spec.strategy_name, spec.parameters, timeframe=spec.strategy_timeframe
                )
                min_hist[spec.key] = int(strat.minimum_history_bars)
            warmup_bars = {
                spec.key: len(
                    [
                        b
                        for b in self._bars(market)
                        if b.market == market
                        and b.timeframe == spec.strategy_timeframe
                        and b.timestamp < WINDOWS[market][0]
                    ]
                )
                for spec in specs
            }
            warmup_equal = len(set(min_hist.values())) == 1 and len(set(warmup_bars.values())) == 1
            # (c) cost parity: identical cost inputs per market (single config;
            #     multiplier recorded in every summary)
            c = self.config
            cost_inputs = {
                "slippage_bps": c.backtest.slippage_bps,
                "spread_bps": c.backtest.spread_bps,
                "commission": c.backtest.commission,
                "crypto_taker_fee_bps": c.backtest.crypto_taker_fee_bps,
                "equity_use_effective_dated_fees": c.backtest.equity_use_effective_dated_fees,
                "equity_taf_per_share": c.backtest.equity_taf_per_share,
                "equity_taf_cap": c.backtest.equity_taf_cap,
                "equity_cat_per_share": c.backtest.equity_cat_per_share,
                "equity_sec_fee_rate": c.backtest.equity_sec_fee_rate,
            }
            summary_multipliers = {k: r["summary_cost_multiplier"] for k, r in runs_1x.items()}
            cost_parity = all(m == 1.0 for m in summary_multipliers.values())
            cost_per_turnover = {k: r["cost_per_unit_turnover"] for k, r in runs_1x.items()}
            # (d) universe parity: same universe hash across candidates
            hashes = {k: r["universe_hash"] for k, r in runs_1x.items()}
            universe_parity = len(set(hashes.values())) == 1
            # (e) no future data: T+1 model spot check on every 1x full run
            t1 = {k: self._t1_check(r) for k, r in runs_1x.items()}
            t1_ok = all(t["same_bar_fill_violations"] == 0 for t in t1.values()) and all(
                t["first_decision"] is not None
                and t["window"][0] <= t["first_decision"] <= t["window"][1]
                for t in t1.values()
            )
            # (f) return comparisons on equal horizons
            durations = {k: r["duration_days"] for k, r in runs_1x.items()}
            equal_horizons = len(set(durations.values())) == 1
            checks[mkey] = {
                "equal_windows": {"pass": equal_windows, "windows": windows},
                "warmup_equivalence": {
                    "pass": warmup_equal,
                    "minimum_history_bars": min_hist,
                    "warmup_bars_before_window_start": warmup_bars,
                    "note": (
                        "same bar set per market; minimum_history_bars depends only on "
                        "frozen params (beta_window_bars+1 / variance_lookback_days+1), "
                        "not on cadence"
                    ),
                },
                "cost_parity": {
                    "pass": cost_parity,
                    "cost_inputs": cost_inputs,
                    "summary_cost_multiplier": summary_multipliers,
                    "cost_per_unit_turnover_1x": cost_per_turnover,
                    "note": (
                        "identical config-level cost model per market; cost_multiplier "
                        "recorded identically in every run summary; residual "
                        "cost-per-turnover differences reflect trade timing/price, "
                        "not cost inputs"
                    ),
                },
                "universe_parity": {
                    "pass": universe_parity,
                    "universe_hash": hashes,
                    "snapshot_count": len(universe.snapshots),
                    "methodology": universe.methodology,
                    "note": "one PointInTimeUniverse built per market, shared by every candidate run",
                },
                "no_future_data": {
                    "pass": t1_ok,
                    "per_candidate": t1,
                    "note": (
                        "engine T+1 model: decisions on closed bars only, fills at next "
                        "tradable bar open; spot check asserts no fill at/after its "
                        "driving decision and all decisions inside the window"
                    ),
                },
                "equal_horizons": {
                    "pass": equal_horizons,
                    "duration_days": durations,
                    "note": "CAGR/Sharpe/Sortino computed by the engine over the identical window for every candidate",
                },
            }
        for mkey in checks:
            print(f"\nMethodology audit — {mkey}:", flush=True)
            rows = []
            for name, info in checks[mkey].items():
                rows.append([name, "PASS" if info["pass"] else "FAIL", _brief_evidence(info)])
            print(_md_table(["check", "result", "evidence (abridged)"], rows), flush=True)
        return checks

    # ------------------------------------------------------------- section 3
    @staticmethod
    def _peer_crosscheck(market: Market, spec: CandidateSpec) -> dict[str, object]:
        """Best-effort comparison against the other agents' JSON artifacts.

        The peer agents (EquityCadenceB / CryptoCadenceC) write under
        data/research/cadence_research_2026_08/{equity,crypto}/ with schemas
        that may differ from ours, so this scans recursively for any dict
        carrying a cost multiplier and a return and whose strategy parameters
        match this candidate (when parameters are present).
        """
        directory = PEER_EQUITY_DIR if market == Market.EQUITY else PEER_CRYPTO_DIR
        if not directory.is_dir():
            return {"available": False, "note": f"no peer artifacts at {directory}"}
        matches: list[dict[str, object]] = []

        def walk(node: object, path: str) -> None:
            if isinstance(node, dict):
                params = node.get("strategy_parameters") or node.get("parameters")
                if isinstance(params, dict) and _params_match(params, spec.parameters):
                    _capture_run(node, path, matches)
                for key, value in node.items():
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{path}[{index}]")

        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            walk(data, path.name)

        if not matches:
            return {"available": False, "note": "no matching candidate entries found in peer JSONs"}
        return {"available": True, "matches": matches}

    @staticmethod
    def _peer_crosscheck_find(
        matches: list[dict[str, object]], multiplier: str
    ) -> list[dict[str, object]]:
        return [m for m in matches if str(m["multiplier"]) == multiplier]

    def section_cost_stress(self, all_records: dict[str, object]) -> dict[str, object]:
        print("\n# SECTION 3 — COST STRESS (1x/2x/3x)", flush=True)
        payload: dict[str, object] = {}
        for market in (Market.EQUITY, Market.CRYPTO):
            if market not in self.markets:
                continue
            mkey = market.value
            market_payload: dict[str, object] = {}
            rows = []
            for spec in CANDIDATES[market]:
                runs = all_records[mkey][spec.key]
                peer = self._peer_crosscheck(market, spec)
                if peer.get("available"):
                    discrepancies = []
                    for match in peer["matches"]:
                        mult = match["multiplier"]
                        if mult not in runs:
                            continue
                        ours = runs[mult]["return"]
                        diff = abs(ours - match["peer_return"])
                        if diff > 1e-4:
                            discrepancies.append(
                                {
                                    "file": match["file"],
                                    "multiplier": mult,
                                    "ours": ours,
                                    "peer": match["peer_return"],
                                    "abs_diff": round(diff, 6),
                                }
                            )
                    peer["discrepancy_count"] = len(discrepancies)
                    peer["discrepancies"] = discrepancies
                market_payload[spec.key] = {
                    "control": spec.control,
                    "parameters": runs["1x"]["strategy_parameters"],
                    "universe_hash": runs["1x"]["universe_hash"],
                    "window": runs["1x"]["window"],
                    "runs": {
                        m: {k: v for k, v in r.items() if k != "_run"} for m, r in runs.items()
                    },
                    "peer_crosscheck": peer,
                }
                for m in ("1x", "2x", "3x"):
                    r = runs[m]
                    rows.append(
                        [
                            spec.key,
                            m,
                            r["return"],
                            r["gross_return"],
                            r["total_cost"],
                            r["fees_pct_of_gross_pnl"]
                            if r["fees_pct_of_gross_pnl"] is not None
                            else float("nan"),
                            r["turnover"],
                            r["cagr"],
                            r["sharpe"],
                            r["max_drawdown"],
                        ]
                    )
            payload[mkey] = market_payload
            print(
                f"\nCost stress — {mkey} (return=gross, fees, fees%% of |gross pnl|, turnover, cagr, sharpe, maxDD):",
                flush=True,
            )
            print(
                _md_table(
                    [
                        "candidate",
                        "x",
                        "net return",
                        "gross return",
                        "fees $",
                        "fees% of |gross pnl|",
                        "turnover",
                        "cagr",
                        "sharpe",
                        "maxDD",
                    ],
                    rows,
                ),
                flush=True,
            )
        return payload

    # ------------------------------------------------------------- section 4
    def section_walkforward(self) -> dict[str, object]:
        print("\n# SECTION 4 — WALK-FORWARD (identical fold sets per market)", flush=True)
        payload: dict[str, object] = {}
        for market in (Market.EQUITY, Market.CRYPTO):
            if market not in self.markets:
                continue
            mkey = market.value
            universe = self._universe(market)
            if market == Market.EQUITY:
                folds = _fold_window(datetime(2019, 1, 2, tzinfo=UTC), EQUITY_END, 6, 24)
            else:
                folds = _calendar_fold_window(datetime(2022, 1, 1, tzinfo=UTC), CRYPTO_END, 6)
            per_candidate: dict[str, object] = {}
            fold_rows: dict[str, list[list[object]]] = {}
            for spec in CANDIDATES[market]:
                fold_records = []
                for index, (fold_start, fold_end) in enumerate(folds, start=1):
                    record = self.run(
                        market,
                        spec,
                        cost_multiplier=1.0,
                        start=fold_start,
                        end=fold_end,
                        universe=universe,
                    )
                    fold_records.append(
                        {
                            "fold": index,
                            "start": fold_start.isoformat(),
                            "end": fold_end.isoformat(),
                            "return": record["return"],
                            "cagr": record["cagr"],
                            "sharpe": record["sharpe"],
                            "sortino": record["sortino"],
                            "max_drawdown": record["max_drawdown"],
                            "turnover": record["turnover"],
                            "trade_count": record["trade_count"],
                            "rebalance_count": record["rebalance_count"],
                            "universe_hash": record["universe_hash"],
                        }
                    )
                returns = [f["return"] for f in fold_records]
                sharpes = [f["sharpe"] for f in fold_records]
                positive = sum(1 for v in returns if v > 0)
                worst = min(fold_records, key=lambda f: f["return"])
                best = max(fold_records, key=lambda f: f["return"])
                per_candidate[spec.key] = {
                    "control": spec.control,
                    "folds": fold_records,
                    "positive_fold_count": positive,
                    "negative_fold_count": len(returns) - positive,
                    "positive_fold_ratio": round(positive / len(returns), 4) if returns else 0.0,
                    "median_fold_return": round(statistics.median(returns), 6) if returns else 0.0,
                    "mean_fold_return": round(statistics.fmean(returns), 6) if returns else 0.0,
                    "worst_fold": {
                        "fold": worst["fold"],
                        "start": worst["start"],
                        "return": worst["return"],
                        "sharpe": worst["sharpe"],
                    },
                    "best_fold": {
                        "fold": best["fold"],
                        "start": best["start"],
                        "return": best["return"],
                        "sharpe": best["sharpe"],
                    },
                    "median_fold_sharpe": round(statistics.median(sharpes), 6) if sharpes else 0.0,
                }
                fold_rows[spec.key] = [
                    [
                        f["fold"],
                        f["start"][:10],
                        f["end"][:10],
                        f["return"],
                        f["sharpe"],
                        f["max_drawdown"],
                        f["trade_count"],
                    ]
                    for f in fold_records
                ]
            # per-fold winner table (baseline vs faster)
            keys = [spec.key for spec in CANDIDATES[market]]
            control_key = next(k for k in keys if per_candidate[k]["control"])
            winner_rows = []
            for index in range(len(folds)):
                row = [
                    index + 1,
                    folds[index][0].isoformat()[:10],
                    folds[index][1].isoformat()[:10],
                ]
                fold_returns = {k: per_candidate[k]["folds"][index]["return"] for k in keys}
                for k in keys:
                    row.append(fold_returns[k])
                best_key = max(keys, key=lambda k: fold_returns[k])
                row.append(best_key)
                faster_keys = [k for k in keys if k != control_key]
                for fk in faster_keys:
                    row.append(
                        "faster"
                        if fold_returns[fk] > fold_returns[control_key]
                        else ("baseline" if fold_returns[control_key] > fold_returns[fk] else "tie")
                    )
                winner_rows.append(row)
            faster_win_counts = {
                fk: sum(
                    1
                    for index in range(len(folds))
                    if per_candidate[fk]["folds"][index]["return"]
                    > per_candidate[control_key]["folds"][index]["return"]
                )
                for fk in faster_keys
            }
            payload[mkey] = {
                "fold_set": "calendar 6-month"
                if market == Market.CRYPTO
                else "30-day-stepped 6-month folds (2019-01-02 anchored)",
                "folds": [
                    {"fold": i + 1, "start": s.isoformat(), "end": e.isoformat()}
                    for i, (s, e) in enumerate(folds)
                ],
                "fold_count": len(folds),
                "universe_hash": hash_point_in_time_universe(universe),
                "per_candidate": per_candidate,
                "per_fold_winner": {
                    "columns": ["fold", "start", "end"]
                    + keys
                    + ["winner"]
                    + [f"{fk}_vs_{control_key}" for fk in faster_keys],
                    "rows": winner_rows,
                },
                "faster_vs_baseline_fold_wins": {
                    f"{fk}_wins_over_{control_key}": faster_win_counts[fk] for fk in faster_keys
                },
            }
            print(
                f"\nWalk-forward — {mkey} ({len(folds)} folds, shared universe {hash_point_in_time_universe(universe)[:16]}...):",
                flush=True,
            )
            print("\nPer-candidate fold stats:", flush=True)
            print(
                _md_table(
                    [
                        "candidate",
                        "positive",
                        "negative",
                        "median return",
                        "worst",
                        "best",
                        "median Sharpe",
                    ],
                    [
                        [
                            k,
                            f"{v['positive_fold_count']}/{len(folds)}",
                            v["negative_fold_count"],
                            v["median_fold_return"],
                            f"{v['worst_fold']['return']:.4f} (fold {v['worst_fold']['fold']})",
                            f"{v['best_fold']['return']:.4f} (fold {v['best_fold']['fold']})",
                            v["median_fold_sharpe"],
                        ]
                        for k, v in per_candidate.items()
                    ],
                ),
                flush=True,
            )
            print("\nPer-fold returns and winner (baseline vs faster):", flush=True)
            print(
                _md_table(
                    ["fold", "start", "end"]
                    + keys
                    + ["winner"]
                    + [f"{fk} vs {control_key}" for fk in faster_keys],
                    winner_rows,
                ),
                flush=True,
            )
        return payload

    # ------------------------------------------------------------- section 5
    @staticmethod
    def _concentration_from_run(record: dict[str, object]) -> dict[str, object]:
        run: ProductBacktestResult = record["_run"]
        contributions = sorted(
            ((item.symbol, item.realized_pnl, item.fees) for item in run.result.per_symbol),
            key=lambda item: (-item[1], item[0]),
        )
        positive_total = sum(pnl for _s, pnl, _f in contributions if pnl > 0)
        top = contributions[:3]
        return {
            "top_1": {
                "symbol": top[0][0],
                "realized_pnl": round(top[0][1], 2),
                "fees": round(top[0][2], 2),
            }
            if top
            else None,
            "top_2": {"symbol": top[1][0], "realized_pnl": round(top[1][1], 2)}
            if len(top) > 1
            else None,
            "top_3": {"symbol": top[2][0], "realized_pnl": round(top[2][1], 2)}
            if len(top) > 2
            else None,
            "top1_share_of_positive_pnl": round(top[0][1] / positive_total, 4)
            if positive_total > 0
            else 0.0,
            "top2_share_of_positive_pnl": round(sum(i[1] for i in top[:2]) / positive_total, 4)
            if positive_total > 0
            else 0.0,
            "top3_share_of_positive_pnl": round(sum(i[1] for i in top[:3]) / positive_total, 4)
            if positive_total > 0
            else 0.0,
            "symbol_count": len(contributions),
            "positive_pnl_total": round(positive_total, 2),
            "max_single_weight": record["maximum_concentration"],
        }

    def _jackknife(
        self, market: Market, spec: CandidateSpec, drop_symbol: str
    ) -> dict[str, object]:
        universe = self._universe(market)
        filtered = PointInTimeUniverse(
            market=market,
            snapshots=tuple(
                replace(
                    s,
                    symbols=frozenset(symbol for symbol in s.symbols if symbol != drop_symbol),
                    observation_set=frozenset(
                        symbol for symbol in s.observation_set if symbol != drop_symbol
                    ),
                )
                for s in universe.snapshots
            ),
            methodology=universe.methodology
            + f" (jackknife: {drop_symbol} excluded from snapshots)",
        )
        start, end = WINDOWS[market]
        base = self.run(
            market,
            spec,
            cost_multiplier=1.0,
            start=start,
            end=end,
            universe=universe,
        )
        jk = self.run(market, spec, cost_multiplier=1.0, start=start, end=end, universe=filtered)
        jk_run: ProductBacktestResult = jk["_run"]
        return {
            "dropped_symbol": drop_symbol,
            "universe_hash_full": base["universe_hash"],
            "universe_hash_jackknife": jk["universe_hash"],
            "snapshots_modified": len(universe.snapshots),
            "base": {k: v for k, v in base.items() if k != "_run"},
            "jackknife": {k: v for k, v in jk.items() if k != "_run"},
            "delta_return": round(jk["return"] - base["return"], 6),
            "delta_cagr": round(jk["cagr"] - base["cagr"], 6),
            "delta_sharpe": round(jk["sharpe"] - base["sharpe"], 6),
            "delta_max_drawdown": round(jk["max_drawdown"] - base["max_drawdown"], 6),
            "top_contributors_after_drop": [
                {"symbol": item.symbol, "realized_pnl": round(item.realized_pnl, 2)}
                for item in sorted(jk_run.result.per_symbol, key=lambda item: -item.realized_pnl)[
                    :3
                ]
            ],
            "caveat": (
                "jackknife removes the symbol from the PIT universe snapshots (symbols and "
                "observation set) via dataclasses.replace on the shared universe; the symbol's "
                "price bars remain in the warmup/bar set (universe membership is the only "
                "thing removed) and the universe hash therefore changes by design"
            ),
        }

    @staticmethod
    def _crypto_pinned_analysis(record: dict[str, object]) -> dict[str, object]:
        run: ProductBacktestResult = record["_run"]
        result = run.result
        btc_rebalances = 0
        eth_rebalances = 0
        total_rebalances = len(result.rebalances)
        btc_weight_sum = 0.0
        eth_weight_sum = 0.0
        for rb in result.rebalances:
            for symbol, weight in rb.executed_weights:
                if symbol == "BTC/USD":
                    btc_rebalances += 1
                    btc_weight_sum += weight
                elif symbol == "ETH/USD":
                    eth_rebalances += 1
                    eth_weight_sum += weight
        contributions = {item.symbol: item.realized_pnl for item in result.per_symbol}
        return {
            "pinned_symbols": ["BTC/USD", "ETH/USD"],
            "total_rebalances": total_rebalances,
            "btc": {
                "rebalances_held": btc_rebalances,
                "share_of_rebalances": round(btc_rebalances / total_rebalances, 4)
                if total_rebalances
                else 0.0,
                "avg_weight_when_held": round(btc_weight_sum / btc_rebalances, 4)
                if btc_rebalances
                else 0.0,
                "realized_pnl": round(contributions.get("BTC/USD", 0.0), 2),
            },
            "eth": {
                "rebalances_held": eth_rebalances,
                "share_of_rebalances": round(eth_rebalances / total_rebalances, 4)
                if total_rebalances
                else 0.0,
                "avg_weight_when_held": round(eth_weight_sum / eth_rebalances, 4)
                if eth_rebalances
                else 0.0,
                "realized_pnl": round(contributions.get("ETH/USD", 0.0), 2),
            },
            "note": (
                "BTC/USD and ETH/USD are pinned universe members (config universe.crypto."
                "pinned_symbols). Low-variance ranking makes BTC the structural parking "
                "spot; the audit documents the pin rather than re-running a jackknife "
                "(dropping a pinned symbol would also violate production universe policy)."
            ),
        }

    def section_concentration(self) -> dict[str, object]:
        print("\n# SECTION 5 — CONCENTRATION", flush=True)
        payload: dict[str, object] = {}
        for market in (Market.EQUITY, Market.CRYPTO):
            if market not in self.markets:
                continue
            mkey = market.value
            market_payload: dict[str, object] = {}
            rows = []
            for spec in CANDIDATES[market]:
                record = self.full_1x_run(market, spec)
                conc = self._concentration_from_run(record)
                entry: dict[str, object] = {
                    "control": spec.control,
                    "parameters": record["strategy_parameters"],
                    "window": record["window"],
                    "universe_hash": record["universe_hash"],
                    "concentration": conc,
                }
                if market == Market.EQUITY:
                    top_symbol = conc["top_1"]["symbol"] if conc["top_1"] else None
                    entry["jackknife"] = (
                        self._jackknife(market, spec, top_symbol) if top_symbol else None
                    )
                else:
                    entry["pinned_analysis"] = self._crypto_pinned_analysis(record)
                market_payload[spec.key] = entry
                rows.append(
                    [
                        spec.key,
                        conc["top_1"]["symbol"] if conc["top_1"] else "-",
                        conc["top_1"]["realized_pnl"] if conc["top_1"] else 0.0,
                        conc["top_2"]["symbol"] if conc["top_2"] else "-",
                        conc["top_2"]["realized_pnl"] if conc["top_2"] else 0.0,
                        conc["top_3"]["symbol"] if conc["top_3"] else "-",
                        conc["top_3"]["realized_pnl"] if conc["top_3"] else 0.0,
                        conc["top1_share_of_positive_pnl"],
                        conc["symbol_count"],
                        conc["max_single_weight"],
                    ]
                )
            payload[mkey] = market_payload
            print(
                f"\nConcentration — {mkey} (top-1/2/3 realized PnL, top1 share of positive PnL, symbol count, max weight):",
                flush=True,
            )
            print(
                _md_table(
                    [
                        "candidate",
                        "top1",
                        "pnl",
                        "top2",
                        "pnl",
                        "top3",
                        "pnl",
                        "top1 share +pnl",
                        "symbols",
                        "max wt",
                    ],
                    rows,
                ),
                flush=True,
            )
        return payload

    # ------------------------------------------------------------- section 6
    def section_calendar_year(self) -> dict[str, object]:
        print("\n# SECTION 6 — CALENDAR-YEAR RETURNS (1x, net of costs)", flush=True)
        payload: dict[str, object] = {}
        for market in (Market.EQUITY, Market.CRYPTO):
            if market not in self.markets:
                continue
            mkey = market.value
            records = {spec.key: self.full_1x_run(market, spec) for spec in CANDIDATES[market]}
            years = sorted(
                {
                    year
                    for record in records.values()
                    for year, _ret, _bench in record["_run"].report.yearly_returns
                }
            )
            headers = ["year"] + [spec.key for spec in CANDIDATES[market]] + ["benchmark"]
            rows = []
            for year in years:
                row: list[object] = [year]
                for spec in CANDIDATES[market]:
                    yearly = dict(
                        (y, (r, b)) for y, r, b in records[spec.key]["_run"].report.yearly_returns
                    )
                    ret, _b = yearly.get(year, (None, None))
                    row.append(ret)
                bench = dict(
                    (y, b) for y, _r, b in records[list(records)[0]]["_run"].report.yearly_returns
                ).get(year, None)
                rows.append(row + [bench])
            payload[mkey] = {
                "benchmark": records[list(records)[0]]["_run"].result.benchmark_symbol,
                "note": (
                    "first (and last) calendar years are partial windows "
                    f"({WINDOWS[market][0].date()}..{WINDOWS[market][1].date()}); returns are net 1x, "
                    "chained from the account start (year 1 anchors at starting equity)"
                ),
                "universe_hash": records[list(records)[0]]["universe_hash"],
                "years": [
                    {
                        "year": r[0],
                        **{
                            spec.key: dict(
                                (y, v)
                                for y, v, _b in records[spec.key]["_run"].report.yearly_returns
                            ).get(r[0])
                            for spec in CANDIDATES[market]
                        },
                        "benchmark": dict(
                            (y, b)
                            for y, _r, b in records[list(records)[0]]["_run"].report.yearly_returns
                        ).get(r[0]),
                    }
                    for r in records[list(records)[0]]["_run"].report.yearly_returns
                ],
            }
            print(
                f"\nCalendar-year returns — {mkey} (benchmark {payload[mkey]['benchmark']}):",
                flush=True,
            )
            print(_md_table(headers, rows), flush=True)
        return payload

    # --------------------------------------------------------------- verdict
    def section_verdict(
        self, cost: dict[str, object], wf: dict[str, object], conc: dict[str, object]
    ) -> dict[str, object]:
        print("\n# VERDICT — INDEPENDENT PARETO PER MARKET", flush=True)
        verdicts: dict[str, object] = {}
        for market in (Market.EQUITY, Market.CRYPTO):
            if market not in self.markets:
                continue
            mkey = market.value
            specs = CANDIDATES[market]
            control = next(s for s in specs if s.control)
            faster = [s for s in specs if not s.control]
            criteria: dict[str, object] = {}
            for fk in faster:
                c1x = cost[mkey][control.key]["runs"]["1x"]
                f1x = cost[mkey][fk.key]["runs"]["1x"]
                c2x = cost[mkey][control.key]["runs"]["2x"]
                f2x = cost[mkey][fk.key]["runs"]["2x"]
                c3x = cost[mkey][control.key]["runs"]["3x"]
                f3x = cost[mkey][fk.key]["runs"]["3x"]
                cagr_up = f1x["cagr"] > c1x["cagr"]
                sharpe_better = f1x["sharpe"] > c1x["sharpe"]
                mdd_better = f1x["max_drawdown"] < c1x["max_drawdown"]
                caputil_better = f1x["avg_exposure"] > c1x["avg_exposure"]
                cost_robust = (
                    (f1x["return"] - f3x["return"]) / abs(f1x["return"]) * 100.0
                    if f1x["return"] != 0
                    else 0.0
                )
                c_cost_robust = (
                    (c1x["return"] - c3x["return"]) / abs(c1x["return"]) * 100.0
                    if c1x["return"] != 0
                    else 0.0
                )
                # Comparative cost robustness: the faster candidate must keep
                # its 1x and 2x edge (2x not materially below the control's 2x)
                # and must not degrade disproportionately 1x->3x relative to
                # the control. (A flat "3x must stay positive" gate would fail
                # the control itself when its own 3x is negative.)
                acceptable_cost = (
                    f1x["return"] > 0
                    and f2x["return"] >= c2x["return"] - 0.005
                    and cost_robust <= c_cost_robust * 1.5 + 25.0
                )
                wf_control = wf[mkey]["per_candidate"][control.key]
                wf_faster = wf[mkey]["per_candidate"][fk.key]
                wf_consistent = (
                    wf_faster["positive_fold_ratio"] >= 0.5 and wf_faster["median_fold_return"] > 0
                )
                wf_wins = wf[mkey]["faster_vs_baseline_fold_wins"].get(
                    f"{fk.key}_wins_over_{control.key}", 0
                )
                conc_control = conc[mkey][control.key]["concentration"]
                conc_faster = conc[mkey][fk.key]["concentration"]
                jk = conc[mkey][fk.key].get("jackknife") if market == Market.EQUITY else None
                concentration_ok = conc_faster["top1_share_of_positive_pnl"] <= 0.6 and (
                    jk is None or abs(jk["delta_return"]) < 0.05
                )
                second_axis = [sharpe_better, mdd_better, caputil_better]
                dominates = (
                    cagr_up
                    and any(second_axis)
                    and acceptable_cost
                    and wf_consistent
                    and concentration_ok
                )
                criteria[fk.key] = {
                    "net_cagr_up": {
                        "pass": cagr_up,
                        "control_cagr": c1x["cagr"],
                        "faster_cagr": f1x["cagr"],
                    },
                    "sharpe_better": {
                        "pass": sharpe_better,
                        "control": c1x["sharpe"],
                        "faster": f1x["sharpe"],
                    },
                    "maxdd_better": {
                        "pass": mdd_better,
                        "control": c1x["max_drawdown"],
                        "faster": f1x["max_drawdown"],
                    },
                    "capital_utilization_better": {
                        "pass": caputil_better,
                        "control_avg_exposure": c1x["avg_exposure"],
                        "faster_avg_exposure": f1x["avg_exposure"],
                    },
                    "cost_robustness_acceptable": {
                        "pass": acceptable_cost,
                        "control_1x_to_3x_relative_loss_pct": round(c_cost_robust, 2),
                        "faster_1x_to_3x_relative_loss_pct": round(cost_robust, 2),
                        "control_1x_2x_3x_returns": [c1x["return"], c2x["return"], c3x["return"]],
                        "faster_1x_2x_3x_returns": [f1x["return"], f2x["return"], f3x["return"]],
                    },
                    "walkforward_consistent": {
                        "pass": wf_consistent,
                        "control_positive_ratio": wf_control["positive_fold_ratio"],
                        "faster_positive_ratio": wf_faster["positive_fold_ratio"],
                        "control_median_fold_return": wf_control["median_fold_return"],
                        "faster_median_fold_return": wf_faster["median_fold_return"],
                        "faster_fold_wins_vs_baseline": wf_wins,
                        "total_folds": wf[mkey]["fold_count"],
                    },
                    "concentration_acceptable": {
                        "pass": concentration_ok,
                        "control_top1_share": conc_control["top1_share_of_positive_pnl"],
                        "faster_top1_share": conc_faster["top1_share_of_positive_pnl"],
                        "jackknife_delta_return": jk["delta_return"] if jk else None,
                    },
                    "dominates": dominates,
                }
            verdicts[mkey] = {
                "control": control.key,
                "faster_candidates": [fk.key for fk in faster],
                "rule": (
                    "faster cadence dominates if net CAGR up AND >=1 of Sharpe/maxDD/"
                    "capital-utilization better AND acceptable cost robustness AND "
                    "walk-forward consistency AND concentration acceptable"
                ),
                "criteria": criteria,
            }
            print(f"\nVerdict — {mkey} (control: {control.key}):", flush=True)
            for fk in faster:
                cr = criteria[fk.key]
                print(f"\n{fk.key} vs {control.key}:", flush=True)
                rows = []
                for name, info in cr.items():
                    if name == "dominates":
                        continue
                    rows.append([name, "PASS" if info["pass"] else "FAIL", _brief_evidence(info)])
                print(_md_table(["criterion", "result", "evidence"], rows), flush=True)
                print(f"  DOMINATES={cr['dominates']}", flush=True)
        return verdicts


def _brief_evidence(info: dict[str, object]) -> str:
    if "note" in info:
        note = str(info["note"])
    else:
        note = ""
    interesting = {
        k: v for k, v in info.items() if k not in ("pass", "note") and not isinstance(v, dict)
    }
    if interesting:
        return "; ".join(f"{k}={v}" for k, v in interesting.items())[:300] + (
            " | " + note if note else ""
        )
    nested = {k: v for k, v in info.items() if k not in ("pass", "note") and isinstance(v, dict)}
    if nested:
        parts = []
        for k, v in nested.items():
            if isinstance(v, dict) and "pass" in v:
                parts.append(f"{k}={'PASS' if v['pass'] else 'FAIL'}")
            else:
                parts.append(f"{k}={v}")
        return "; ".join(parts)[:300] + (" | " + note if note else "")
    return note[:300]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        default="baseline,cost,walkforward,concentration,calendar,methodology,verdict",
        help="comma-separated sections to run",
    )
    parser.add_argument(
        "--markets", default="equity,crypto", help="comma-separated markets (equity,crypto)"
    )
    args = parser.parse_args()
    sections = {s.strip() for s in args.only.split(",") if s.strip()}
    market_set = set()
    for name in args.markets.split(","):
        name = name.strip()
        if name == "equity":
            market_set.add(Market.EQUITY)
        elif name == "crypto":
            market_set.add(Market.CRYPTO)
        elif name:
            raise SystemExit(f"unknown market {name!r}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()
    runner = AuditRunner(config, market_set)

    results: dict[str, object] = {}

    # Cost stress always runs first: it provides the 1x full-window runs that
    # baseline / methodology / calendar-year / concentration reuse.
    if "cost" in sections or "baseline" in sections or "methodology" in sections:
        all_records: dict[str, object] = {}
        for market in (Market.EQUITY, Market.CRYPTO):
            if market not in market_set:
                continue
            all_records[market.value] = {
                spec.key: runner.cost_stress_runs(market, spec) for spec in CANDIDATES[market]
            }
        results["cost_stress"] = runner.section_cost_stress(all_records)
        _write("cost_stress.json", results["cost_stress"])

    if "baseline" in sections:
        results["baseline_reproduction"] = runner.section_baseline()
        _write("baseline_reproduction.json", results["baseline_reproduction"])

    if "methodology" in sections and "cost_stress" in results:
        results["methodology_audit"] = runner.section_methodology(all_records)
        _write("methodology_audit.json", results["methodology_audit"])

    if "walkforward" in sections:
        results["walk_forward"] = runner.section_walkforward()
        _write("walk_forward.json", results["walk_forward"])

    if "concentration" in sections:
        results["concentration"] = runner.section_concentration()
        _write("concentration.json", results["concentration"])

    if "calendar" in sections:
        results["calendar_year"] = runner.section_calendar_year()
        _write("calendar_year.json", results["calendar_year"])

    if "verdict" in sections:
        needed = {"cost_stress", "walk_forward", "concentration"}
        for name in needed - set(results):
            path = OUT_DIR / f"{name}.json"
            if path.exists():
                results[name] = json.loads(path.read_text(encoding="utf-8"))
        if needed <= set(results):
            results["verdict"] = runner.section_verdict(
                results["cost_stress"], results["walk_forward"], results["concentration"]
            )
            _write("verdict.json", results["verdict"])

    # Determinism/coverage metadata for the whole audit.
    universe_hashes: dict[str, str] = {}
    universe_snapshot_counts: dict[str, int] = {}
    for market in market_set:
        if market in runner.universes:
            universe_hashes[market.value] = hash_point_in_time_universe(runner.universes[market])
            universe_snapshot_counts[market.value] = len(runner.universes[market].snapshots)
        else:
            cost_path = OUT_DIR / "cost_stress.json"
            if cost_path.exists():
                try:
                    saved = json.loads(cost_path.read_text(encoding="utf-8"))
                    if market.value in saved:
                        first = next(iter(saved[market.value].values()))
                        universe_hashes[market.value] = str(first["universe_hash"])
                except (OSError, json.JSONDecodeError, KeyError, StopIteration):
                    pass
    meta: dict[str, object] = {
        "repo_head": _git_head(),
        "generated_utc": datetime.now(UTC).isoformat(),
        "datasets": {m.value: str(DATASETS[m][0].name) for m in market_set},
        "windows": {
            m.value: [WINDOWS[m][0].isoformat(), WINDOWS[m][1].isoformat()] for m in market_set
        },
        "universe_hashes": universe_hashes,
        "universe_snapshot_counts": universe_snapshot_counts,
        "candidates": {
            m.value: [
                {
                    "key": s.key,
                    "strategy": s.strategy_name,
                    "parameters": s.parameters,
                    "timeframe": s.strategy_timeframe,
                    "control": s.control,
                }
                for s in CANDIDATES[m]
            ]
            for m in market_set
        },
        "sections_run": sorted(sections),
    }
    _write("audit_meta.json", meta)
    print("\nWrote JSON outputs to", OUT_DIR, flush=True)
    return 0


def _git_head() -> str:
    try:
        import subprocess

        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _write(name: str, payload: object) -> None:
    path = OUT_DIR / name
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
