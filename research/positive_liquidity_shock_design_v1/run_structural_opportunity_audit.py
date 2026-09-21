#!/usr/bin/env python3
"""Outcome-blind structural audit for the Bali et al. liquidity shock.

The script reads only contemporaneous OHLCV, lifetimes/corporate-action metadata,
and production scanner constants.  It never reads a future return, P&L, NAV, or
candidate performance output.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BARS = ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
BARS_MANIFEST = BARS.with_suffix(".manifest.json")
ACTIONS = ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
LIFETIMES = ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv"
OUTPUT = Path(__file__).with_name("STRUCTURAL_OPPORTUNITY_AUDIT.json")
CANONICAL_ENDPOINT = date(2026, 7, 30)
RESERVED = {"SPY", "SH", "PSQ", "DOG"}


@dataclass
class MonthAccumulator:
    terms: list[float] = field(default_factory=list)
    abs_returns: list[float] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    dollar_volumes: list[float] = field(default_factory=list)


@dataclass
class SymbolState:
    current_symbol: str = ""
    previous_close: float | None = None
    current: MonthAccumulator = field(default_factory=MonthAccumulator)
    monthly: deque[dict[str, float | int]] = field(default_factory=lambda: deque(maxlen=13))
    trailing_bars: deque[tuple[date, float, float, float, float]] = field(default_factory=deque)


@dataclass(frozen=True)
class LifetimeInterval:
    entity_id: str
    valid_from: date
    valid_to: date


def load_lifetimes() -> dict[str, list[LifetimeInterval]]:
    by_symbol: dict[str, list[LifetimeInterval]] = defaultdict(list)
    with LIFETIMES.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            historical_symbol = str(row["historical_symbol"]).upper()
            valid_from = date.fromisoformat(str(row["valid_from"])[:10])
            valid_to_raw = str(row.get("valid_to") or row["last_observed_bar"])
            valid_to = date.fromisoformat(valid_to_raw[:10])
            by_symbol[historical_symbol].append(
                LifetimeInterval(str(row["entity_id"]), valid_from, valid_to)
            )
    return by_symbol


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def month_index(month: str) -> int:
    year, number = (int(part) for part in month.split("-"))
    return year * 12 + number


def quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def correlation(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 2:
        return None
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    denominator = math.sqrt(
        sum((x - x_mean) ** 2 for x in xs) * sum((y - y_mean) ** 2 for y in ys)
    )
    return numerator / denominator if denominator else None


def scanner_eligible(state: SymbolState, month_end: date) -> tuple[bool, float]:
    cutoff = date.fromordinal(month_end.toordinal() - 60)
    trailing = [bar for bar in state.trailing_bars if cutoff <= bar[0] <= month_end]
    valid = trailing[-20:]
    if not valid:
        return False, 0.0
    price = valid[-1][3]
    dollar_volumes = [bar[3] * bar[4] for bar in valid]
    median_dollar_volume = statistics.median(dollar_volumes)
    true_ranges: list[float] = []
    prior_close: float | None = None
    for _day, high, low, close, _volume in valid:
        true_ranges.append(
            high - low
            if prior_close is None
            else max(high - low, abs(high - prior_close), abs(low - prior_close))
        )
        prior_close = close
    atr_pct = statistics.fmean(true_ranges) / price if price > 0 else math.inf
    complete = len(valid) / 20
    eligible = (
        price >= 5
        and median_dollar_volume >= 20_000_000
        and 0.005 <= atr_pct <= 0.12
        and complete >= 0.95
    )
    return eligible, median_dollar_volume


def finalize_month(
    month: str,
    month_end: date,
    states: dict[str, SymbolState],
    relationships: dict[str, list[tuple[float, float]]],
) -> dict[str, Any]:
    scores: dict[str, float] = {}
    scanner_metrics: list[tuple[float, str, bool]] = []
    valid_illiq = 0
    valid_days: list[int] = []
    for entity_id, state in states.items():
        acc = state.current
        if len(acc.terms) >= 15:
            illiq = statistics.fmean(acc.terms) * 1_000_000
            record: dict[str, float | int] = {
                "month_index": month_index(month),
                "illiq": illiq,
                "valid_days": len(acc.terms),
                "mean_abs_return": statistics.fmean(acc.abs_returns),
                "mean_dollar_volume": statistics.fmean(acc.dollar_volumes),
                "return_volatility": statistics.pstdev(acc.returns),
            }
            valid_illiq += 1
            valid_days.append(len(acc.terms))
            previous = list(state.monthly)[-12:]
            exact_history = (
                len(previous) == 12
                and [int(item["month_index"]) for item in previous]
                == list(range(month_index(month) - 12, month_index(month)))
            )
            if exact_history:
                prior_illiq = [float(item["illiq"]) for item in previous]
                score = -(illiq - statistics.fmean(prior_illiq))
                scores[entity_id] = score
                relationships["current_illiq"].append((score, illiq))
                relationships["mean_abs_return"].append((score, float(record["mean_abs_return"])))
                relationships["log_mean_dollar_volume"].append(
                    (score, math.log(float(record["mean_dollar_volume"])))
                )
                relationships["return_volatility"].append(
                    (score, float(record["return_volatility"]))
                )
                prior_abs = [float(item["mean_abs_return"]) for item in previous]
                prior_dv = [math.log(float(item["mean_dollar_volume"])) for item in previous]
                relationships["abs_return_change_from_prior_mean"].append(
                    (score, float(record["mean_abs_return"]) - statistics.fmean(prior_abs))
                )
                relationships["log_dollar_volume_change_from_prior_mean"].append(
                    (score, math.log(float(record["mean_dollar_volume"])) - statistics.fmean(prior_dv))
                )
            state.monthly.append(record)
        state.current = MonthAccumulator()
        eligible, median_dv = scanner_eligible(state, month_end)
        scanner_metrics.append((median_dv, entity_id, eligible))

    stage_a = {symbol for _dv, symbol, _eligible in sorted(scanner_metrics, key=lambda x: (-x[0], x[1]))[:300]}
    scanner_eligibility = {symbol: eligible for _dv, symbol, eligible in scanner_metrics}
    scanner_scoreable = [
        score
        for entity_id, score in scores.items()
        if entity_id in stage_a
        and states[entity_id].current_symbol not in RESERVED
        and scanner_eligibility.get(entity_id, False)
    ]
    return {
        "month": month,
        "valid_monthly_illiq_symbols": valid_illiq,
        "scoreable_exact_12_month_history": len(scores),
        "scanner_stage_a_scoreable": len(scanner_scoreable),
        "top_six_available": len(scanner_scoreable) >= 6,
        "one_slot_available": bool(scanner_scoreable),
        "valid_days_median": statistics.median(valid_days) if valid_days else None,
        "scanner_score_p10": quantile(scanner_scoreable, 0.10),
        "scanner_score_median": quantile(scanner_scoreable, 0.50),
        "scanner_score_p90": quantile(scanner_scoreable, 0.90),
        "scanner_score_exact_top_tie_count": (
            sum(score == max(scanner_scoreable) for score in scanner_scoreable) if scanner_scoreable else 0
        ),
    }


def synthetic_split_invariance() -> list[dict[str, Any]]:
    fixtures = [("forward_4_for_1", 4.0), ("reverse_1_for_8", 1 / 8)]
    rows = []
    raw_previous_close = 117.6470588235294
    raw_close = 120.0
    raw_volume = 1_000_000.0
    raw_return = raw_close / raw_previous_close - 1
    raw_dv = raw_close * raw_volume
    raw_term = abs(raw_return) / raw_dv
    for name, share_factor in fixtures:
        adjusted_previous_close = raw_previous_close / share_factor
        adjusted_close = raw_close / share_factor
        adjusted_volume = raw_volume * share_factor
        adjusted_return = adjusted_close / adjusted_previous_close - 1
        adjusted_dv = adjusted_close * adjusted_volume
        adjusted_term = abs(adjusted_return) / adjusted_dv
        rows.append(
            {
                "fixture": name,
                "share_factor": share_factor,
                "raw_dollar_volume": raw_dv,
                "adjusted_dollar_volume": adjusted_dv,
                "raw_return": raw_return,
                "adjusted_return": adjusted_return,
                "raw_illiq_term": raw_term,
                "adjusted_illiq_term": adjusted_term,
                "return_invariant": math.isclose(raw_return, adjusted_return),
                "dollar_volume_invariant": raw_dv == adjusted_dv,
                "illiq_invariant": math.isclose(raw_term, adjusted_term),
            }
        )
    return rows


def main() -> None:
    lifetimes = load_lifetimes()
    states: dict[str, SymbolState] = defaultdict(SymbolState)
    relationships: dict[str, list[tuple[float, float]]] = defaultdict(list)
    month_rows: list[dict[str, Any]] = []
    current_month: str | None = None
    current_month_end: date | None = None
    total_rows = 0
    rows_through_endpoint = 0
    excluded_partial_endpoint_rows = 0
    zero_volume_rows = 0
    invalid_ohlcv_rows = 0
    lifetime_unmatched_rows = 0
    lifetime_ambiguous_rows = 0
    fixture_rows: dict[tuple[str, date], dict[str, float]] = {}
    fixture_keys = {
        ("AAPL", date(2020, 8, 28)),
        ("AAPL", date(2020, 8, 31)),
        ("GE", date(2021, 7, 30)),
        ("GE", date(2021, 8, 2)),
    }
    previous_session_day: date | None = None
    symbols_seen_on_day: set[str] = set()

    with BARS.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            total_rows += 1
            day = date.fromisoformat(str(row["timestamp"])[:10])
            if day > CANONICAL_ENDPOINT:
                excluded_partial_endpoint_rows += 1
                continue
            rows_through_endpoint += 1
            symbol = str(row["symbol"]).upper()
            if previous_session_day is not None and day < previous_session_day:
                raise RuntimeError("bar file is not ordered by session date")
            if day != previous_session_day:
                symbols_seen_on_day.clear()
                previous_session_day = day
            if symbol in symbols_seen_on_day:
                raise RuntimeError(f"duplicate bar for {symbol} on {day}")
            symbols_seen_on_day.add(symbol)
            matching_lifetimes = [
                interval
                for interval in lifetimes.get(symbol, [])
                if interval.valid_from <= day <= interval.valid_to
            ]
            if not matching_lifetimes:
                lifetime_unmatched_rows += 1
                continue
            if len(matching_lifetimes) != 1:
                lifetime_ambiguous_rows += 1
                continue
            entity_id = matching_lifetimes[0].entity_id
            month = day.strftime("%Y-%m")
            if current_month is None:
                current_month = month
            if month != current_month:
                assert current_month_end is not None
                month_rows.append(finalize_month(current_month, current_month_end, states, relationships))
                current_month = month
            current_month_end = day
            try:
                high = float(row["high"])
                low = float(row["low"])
                close = float(row["close"])
                volume = float(row["volume"])
            except (TypeError, ValueError):
                invalid_ohlcv_rows += 1
                continue
            if volume == 0:
                zero_volume_rows += 1
            if not all(math.isfinite(value) for value in (high, low, close, volume)) or close <= 0 or volume < 0:
                invalid_ohlcv_rows += 1
                continue
            state = states[entity_id]
            state.current_symbol = symbol
            state.trailing_bars.append((day, high, low, close, volume))
            cutoff_ordinal = day.toordinal() - 65
            while state.trailing_bars and state.trailing_bars[0][0].toordinal() < cutoff_ordinal:
                state.trailing_bars.popleft()
            if volume > 0 and state.previous_close is not None and state.previous_close > 0:
                daily_return = close / state.previous_close - 1
                dollar_volume = close * volume
                if math.isfinite(daily_return) and dollar_volume > 0:
                    state.current.returns.append(daily_return)
                    state.current.abs_returns.append(abs(daily_return))
                    state.current.dollar_volumes.append(dollar_volume)
                    state.current.terms.append(abs(daily_return) / dollar_volume)
            state.previous_close = close
            if (symbol, day) in fixture_keys:
                fixture_rows[(symbol, day)] = {
                    "close": close,
                    "volume": volume,
                    "dollar_volume": close * volume,
                }

    if current_month is not None and current_month_end is not None:
        month_rows.append(finalize_month(current_month, current_month_end, states, relationships))

    eligible_months = [row for row in month_rows if int(str(row["month"])[:4]) >= 2017]
    scanner_counts = [int(row["scanner_stage_a_scoreable"]) for row in eligible_months]
    action_counts: dict[str, int] = defaultdict(int)
    with ACTIONS.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ex_date = date.fromisoformat(str(row["ex_date"]))
            if ex_date <= CANONICAL_ENDPOINT:
                action_counts[str(row["action_type"])] += 1

    manifest = json.loads(BARS_MANIFEST.read_text(encoding="utf-8"))
    payload = {
        "schema_version": 1,
        "status": "STRUCTURAL_ONLY_NO_FUTURE_RETURNS",
        "formula_diagnostic": {
            "daily_return": "simple split-adjusted close-to-close price return; no future bar and no dividend augmentation",
            "daily_illiq": "abs(return) / (split-adjusted close * split-adjusted volume)",
            "monthly_illiq": "mean(valid daily terms) * 1e6; at least 15 valid daily returns",
            "liquidity_shock": "-(ILLIQ_t - mean(ILLIQ_t-12..t-1)); no volatility standardization in the final RFS paper",
            "history": "exactly 12 consecutive valid prior calendar months (a fail-closed local rule because the paper gives no monthly missing-history rule)",
            "warning": "The structural score is diagnostic until the primary-paper return convention and local dividend basis are accepted.",
        },
        "source_receipts": {
            "bars": {
                "path": str(BARS.relative_to(ROOT)),
                "sha256": sha256(BARS),
                "manifest_sha256": sha256(BARS_MANIFEST),
                "manifest_feed": manifest.get("feed"),
                "manifest_adjustment": manifest.get("adjustment"),
            },
            "lifetimes": {"path": str(LIFETIMES.relative_to(ROOT)), "sha256": sha256(LIFETIMES)},
            "corporate_actions": {"path": str(ACTIONS.relative_to(ROOT)), "sha256": sha256(ACTIONS)},
        },
        "row_quality": {
            "total_rows": total_rows,
            "rows_through_canonical_endpoint": rows_through_endpoint,
            "excluded_partial_2026_07_31_rows": excluded_partial_endpoint_rows,
            "zero_volume_rows": zero_volume_rows,
            "invalid_scanner_ohlcv_rows": invalid_ohlcv_rows,
            "invalid_amihud_rows": invalid_ohlcv_rows + zero_volume_rows,
            "zero_volume_fraction_through_endpoint": zero_volume_rows / rows_through_endpoint,
            "lifetime_unmatched_rows_excluded": lifetime_unmatched_rows,
            "lifetime_ambiguous_rows_excluded": lifetime_ambiguous_rows,
            "duplicate_symbol_session_policy": "raise and emit no audit",
        },
        "scanner_contract": {
            "stage_a": "top 300 by trailing 20 finite bars (including zero volume) median close*volume",
            "minimum_price": 5,
            "minimum_median_dollar_volume": 20_000_000,
            "minimum_data_completeness": 0.95,
            "atr_pct_range": [0.005, 0.12],
            "historical_metadata_degradation": "spread, complete tradability, sector, security type, and exchange are unavailable",
        },
        "summary_2017_through_endpoint": {
            "months": len(eligible_months),
            "months_with_at_least_six_scanner_scoreable": sum(bool(row["top_six_available"]) for row in eligible_months),
            "months_with_at_least_one_scanner_scoreable": sum(bool(row["one_slot_available"]) for row in eligible_months),
            "scanner_scoreable_min": min(scanner_counts) if scanner_counts else None,
            "scanner_scoreable_median": statistics.median(scanner_counts) if scanner_counts else None,
            "scanner_scoreable_max": max(scanner_counts) if scanner_counts else None,
        },
        "contemporaneous_component_correlations": {
            key: {"pearson": correlation(pairs), "observations": len(pairs)}
            for key, pairs in sorted(relationships.items())
        },
        "split_audit": {
            "corporate_action_counts_through_endpoint": dict(sorted(action_counts.items())),
            "synthetic_basis_invariance": synthetic_split_invariance(),
            "observed_real_event_rows": {
                f"{symbol}_{day.isoformat()}": values
                for (symbol, day), values in sorted(fixture_rows.items())
            },
            "real_event_scope": "Observed adjacent stored rows demonstrate price-basis continuity, not the provider's unadjusted-to-adjusted transformation algorithm.",
        },
        "monthly_structural_coverage": eligible_months,
        "prohibited_inputs_read": {
            "forward_returns": False,
            "pnl_or_nav": False,
            "v3_outcomes": False,
            "economic_parameter_comparisons": False,
        },
        "limitations": [
            "The historical broad US_EQUITY bars have no complete PIT security-type taxonomy.",
            "Historical spread and fully effective-dated tradability metadata are absent, so scanner reconstruction is conservative structural parity, not exact production snapshot identity.",
            "The source manifest says split adjustment, but it does not independently encode the provider's price-volume adjustment algorithm.",
            "The diagnostic uses split-adjusted close-to-close price returns and does not manufacture a dividend return from potentially mismatched historical cash-dividend/share bases.",
            "Contemporaneous correlations diagnose mechanical overlap only and have no predictive or economic interpretation.",
        ],
    }
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
