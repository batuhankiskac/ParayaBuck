"""Run frozen, non-PIT crypto diagnostics without changing candidate gates.

The runner uses only receipt-bound Coin Metrics PriceUSD as a reference return
series and the already captured causal fields.  Because every source row has a
blank ``available_at`` (and some native units are undocumented in the response),
all outputs are explicitly retrospective diagnostics.  No result can make a
candidate admitted or eligible.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/research_runs/crypto_max_testing_2026_09_09/diagnostics"
PROTOCOL = OUT / "protocol_v2.json"
RESULTS = OUT / "diagnostic_results.json"
RECEIPT = OUT / "validation_receipt.json"
REPORT = OUT / "diagnostic_report.md"
REFERENCE = REPO / "data/research_runs/crypto_max_testing_2026_09_09/routes/reference_manifest.json"
REFERENCE_CSV = REPO / "data/research_runs/crypto_max_testing_2026_09_09/routes/coinmetrics_btc_eth_priceusd_1d_2022_2026-07-31.csv"
V3 = REPO / "data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/current_v3_daily_nav_comparability.json"
K01 = REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K01_MVRV_BTC_ETH_1d.csv"
K03 = REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K03_FEE_USAGE_BTC_ETH_1d.csv"
K04 = REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K04_SUPPLY_ISSUANCE_BTC_ETH_1d.csv"
K11 = REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K11_binance_aggtrades_pilot.csv"
ROUTE_MANIFEST = REPO / "data/research_runs/crypto_max_testing_2026_09_09/routes/route_manifest.json"
START = date(2022, 1, 1)
TRAIN_END = date(2024, 12, 31)
HOLDOUT_START = date(2025, 1, 1)
END = date(2026, 7, 30)
LAGS = (1, 3, 7)
COST_MULTIPLIERS = (1, 2, 3)
BASE_COST = 0.001


def sha(value: Path | bytes) -> str:
    data = value.read_bytes() if isinstance(value, Path) else value
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def dparse(value: str) -> date:
    return date.fromisoformat(value[:10])


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def freeze_protocol() -> dict[str, Any]:
    rules = {
        "K01": {
            "feature": "native CapMVRVCur; within-asset value",
            "formula": "consume CapMVRVCur directly",
            "direction": "long when value <= 1.0; cash otherwise",
            "threshold": {"operator": "<=", "value": 1.0},
            "units_policy": "ratio; dimensionless",
        },
        "K03": {
            "feature": "native FeeTotNtv 30-day log growth",
            "formula": "log1p(FeeTotNtv_t)-log1p(FeeTotNtv_t-30d)",
            "direction": "long when growth > 0; cash otherwise",
            "threshold": {"operator": ">", "value": 0.0},
            "units_policy": "log growth; dimensionless; no Sybil-adjustment claim",
        },
        "K04": {
            "feature": "gross IssTotNtv / prior SplyCur",
            "formula": "IssTotNtv_t / SplyCur_(t-1)",
            "direction": "long when gross issuance ratio <= 0.0001; cash otherwise",
            "threshold": {"operator": "<=", "value": 0.0001},
            "units_policy": "same-asset native units ratio; diagnostic only; not net dilution",
        },
        "K09": {
            "feature": "Kraken venue/instrument basis plus OI change; separate funding branch",
            "formula": "z_basis + z_OI_change <= -1.0; separate funding branch uses z_basis + z_funding <= -1.0",
            "direction": "long spot when the standardized crowding sum is <= -1.0; cash otherwise",
            "threshold": {"operator": "<=", "value": -1.0, "lookback": "expanding prior observations; minimum 20"},
            "units_policy": "within-series standardized transforms only; raw OI/basis units are not asserted",
        },
        "K11": {
            "feature": "Binance USD-M BTCUSDT signed aggregate-trade notional",
            "formula": "sum(price*quantity*(+1 if is_buyer_maker=false else -1)) per UTC day",
            "direction": "long spot when signed flow > 0; cash otherwise",
            "threshold": {"operator": ">", "value": 0.0},
            "units_policy": "native notional; one-day archive diagnostic only",
        },
    }
    payload = {
        "schema": "crypto-max-diagnostic-protocol-v2",
        "version": "2026-09-09",
        "frozen_before_outcomes": True,
        "historical_window": "2022-01-01..2026-07-31",
        "train_window": "2022-01-01..2024-12-31",
        "recent_window": "2024-01-01..2026-07-31",
        "holdout_window": "2025-01-01..2026-07-30",
        "lags_days": list(LAGS),
        "cost_stress": {"multipliers": list(COST_MULTIPLIERS), "base_one_way_turnover": BASE_COST},
        "bootstrap": {"kind": "5-day circular block", "replicates": 256, "seed": 20260909},
        "permutation": {"kind": "candidate-return permutation placebo correlation", "replicates": 128, "seed": 20260910},
        "overlay": "0.95 current V3 daily NAV return + 0.05 candidate diagnostic net return",
        "constraints": {"equity_max": 0.55, "crypto_max": 0.05, "gross_max": 0.60, "cash_min": 0.40, "max_crypto_positions": 2, "max_total_positions": 6},
        "availability": "all source rows have blank available_at; lagged reconstructions are not causal PIT and remain diagnostic-only",
        "methodology_sources": [
            "https://docs.coinmetrics.io/asset-metrics/market/capact1yrusd",
            "https://docs.coinmetrics.io/asset-metrics/transactions/txtfrvaladjntv",
            "https://docs.coinmetrics.io/asset-metrics/supply/ser",
            "https://docs.coinmetrics.io/resources/faqs",
            "https://docs.kraken.com/api/docs/futures-api/charts/market-analytics",
        ],
        "reference_source": {"path": str(REFERENCE.relative_to(REPO)), "sha256": sha(REFERENCE)},
        "feature_sources": [
            {"path": str(path.relative_to(REPO)), "sha256": sha(path)}
            for path in (K01, K03, K04, K11, ROUTE_MANIFEST)
        ],
        "rules": rules,
        "economic_run": False,
        "admitted": 0,
        "eligible": False,
        "promotion": False,
        "protocol_sha256": None,
    }
    payload["protocol_sha256"] = sha(canonical({k: v for k, v in payload.items() if k != "protocol_sha256"}))
    OUT.mkdir(parents=True, exist_ok=True)
    PROTOCOL.write_bytes(canonical(payload))
    return payload


def load_prices() -> dict[str, dict[date, float]]:
    rows = read_csv(REFERENCE_CSV)
    result: dict[str, dict[date, float]] = {"btc": {}, "eth": {}}
    for row in rows:
        asset = row["asset"]
        if asset in result:
            result[asset][dparse(row["event_time"])] = float(row["price_usd"])
    if any(len(result[a]) != 1673 for a in result):
        raise ValueError("reference_price_coverage")
    return result


def load_v3() -> dict[date, float]:
    payload = json.loads(V3.read_text(encoding="utf-8"))
    rows = payload.get("daily_nav")
    if not isinstance(rows, list) or not rows:
        raise ValueError("v3_daily_nav_missing")
    nav = {dparse(str(row["date"])): float(row["nav"]) for row in rows}
    return {day: nav[day] / nav[prev] - 1.0 for prev, day in zip(sorted(nav)[:-1], sorted(nav)[1:]) if day >= START and day <= END and prev in nav}


def feature_map(path: Path, metric: str) -> dict[str, dict[date, float]]:
    result: dict[str, dict[date, float]] = {"btc": {}, "eth": {}}
    for row in read_csv(path):
        if row.get("metric") == metric and row.get("asset") in result:
            result[row["asset"]][dparse(row["event_time"])] = float(row["value"])
    return result


def k03_features() -> dict[str, dict[date, float]]:
    raw = feature_map(K03, "FeeTotNtv")
    out: dict[str, dict[date, float]] = {"btc": {}, "eth": {}}
    for asset, values in raw.items():
        for day, value in values.items():
            previous = day - timedelta(days=30)
            if previous in values and all((previous + timedelta(days=i)) in values for i in range(31)):
                out[asset][day] = math.log1p(value) - math.log1p(values[previous])
    return out


def k04_features() -> dict[str, dict[date, float]]:
    rows = read_csv(K04)
    issuance: dict[tuple[str, date], float] = {}
    supply: dict[tuple[str, date], float] = {}
    for row in rows:
        key = (row["asset"], dparse(row["event_time"]))
        if row["metric"] == "IssTotNtv":
            issuance[key] = float(row["value"])
        elif row["metric"] == "SplyCur":
            supply[key] = float(row["value"])
    out: dict[str, dict[date, float]] = {"btc": {}, "eth": {}}
    for (asset, day), value in issuance.items():
        previous = day - timedelta(days=1)
        if (asset, previous) in supply and supply[(asset, previous)] > 0:
            out[asset][day] = value / supply[(asset, previous)]
    return out


def k11_features() -> dict[str, dict[date, float]]:
    totals: dict[date, float] = {}
    for row in read_csv(K11):
        day = dparse(row["event_time_utc"])
        signed = float(row["price"]) * float(row["quantity"]) * (-1.0 if row["is_buyer_maker"].lower() == "true" else 1.0)
        totals[day] = totals.get(day, 0.0) + signed
    return {"btc": totals}


def _payload_for_probe(probe: dict[str, Any]) -> tuple[list[int], dict[str, Any]]:
    payload = json.loads((REPO / probe["body"]).read_text(encoding="utf-8"))
    result = payload.get("result", {})
    return result.get("timestamp", []), result.get("data", {})


def k09_features() -> tuple[dict[str, dict[date, float]], dict[str, dict[date, float]]]:
    manifest = json.loads(ROUTE_MANIFEST.read_text(encoding="utf-8"))
    basis: dict[str, dict[date, float]] = {"btc": {}, "eth": {}}
    oi: dict[str, dict[date, float]] = {"btc": {}, "eth": {}}
    funding: dict[str, dict[date, float]] = {"btc": {}, "eth": {}}
    for probe in manifest["probes"]:
        if probe.get("kind") != "kraken_market_analytics_long_history":
            continue
        symbol = str(probe["slug"]).split("_")[2]
        asset = "btc" if symbol == "xbtusd" else "eth"
        timestamps, values = _payload_for_probe(probe)
        unit = "milliseconds" if timestamps and int(timestamps[0]) > 10**12 else "seconds"
        for index, stamp in enumerate(timestamps):
            day = datetime.fromtimestamp(int(stamp) / (1000 if unit == "milliseconds" else 1), tz=UTC).date()
            if probe["slug"].find("future_basis") >= 0:
                series = values.get("basis", []) if isinstance(values, dict) else []
                if index < len(series):
                    basis[asset][day] = float(series[index])
            elif probe["slug"].find("open_interest") >= 0:
                series = values if isinstance(values, list) else []
                if index < len(series) and isinstance(series[index], list) and series[index]:
                    oi[asset][day] = float(series[index][-1])
            elif probe["slug"].find("funding") >= 0:
                series = values.get("rate", []) if isinstance(values, dict) else []
                if index < len(series) and isinstance(series[index], list) and series[index]:
                    funding[asset][day] = float(series[index][-1])
    base: dict[str, dict[date, float]] = {"btc": {}, "eth": {}}
    fund: dict[str, dict[date, float]] = {"btc": {}, "eth": {}}
    for asset in base:
        previous_oi: float | None = None
        prior_basis: list[float] = []
        prior_change: list[float] = []
        prior_funding: list[float] = []
        for day in sorted(set(basis[asset]) & set(oi[asset])):
            change = None if previous_oi is None else oi[asset][day] - previous_oi
            if change is not None and len(prior_basis) >= 20 and len(prior_change) >= 20:
                def z(value: float, values: list[float]) -> float:
                    stdev = statistics.pstdev(values)
                    return (value - statistics.fmean(values)) / stdev if stdev else 0.0
                base[asset][day] = z(basis[asset][day], prior_basis) + z(change, prior_change)
            prior_basis.append(basis[asset][day])
            if change is not None:
                prior_change.append(change)
            previous_oi = oi[asset][day]
        previous_basis: list[float] = []
        for day in sorted(set(basis[asset]) & set(funding[asset])):
            if len(previous_basis) >= 20 and len(prior_funding) >= 20:
                basis_sd = statistics.pstdev(previous_basis)
                fund_sd = statistics.pstdev(prior_funding)
                z_basis = (basis[asset][day] - statistics.fmean(previous_basis)) / basis_sd if basis_sd else 0.0
                z_fund = (funding[asset][day] - statistics.fmean(prior_funding)) / fund_sd if fund_sd else 0.0
                fund[asset][day] = z_basis + z_fund
            previous_basis.append(basis[asset][day])
            prior_funding.append(funding[asset][day])
    return base, fund


def _returns(prices: dict[str, dict[date, float]]) -> dict[str, dict[date, float]]:
    return {asset: {day: values[day + timedelta(days=1)] / values[day] - 1.0 for day in values if day < END and day + timedelta(days=1) in values} for asset, values in prices.items()}


def _metric(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"days": 0, "mean": None, "cagr": None, "max_drawdown": None, "tail_p05": None, "sharpe": None}
    curve = 1.0
    peak = 1.0
    max_dd = 0.0
    for value in values:
        curve *= 1.0 + value
        peak = max(peak, curve)
        max_dd = min(max_dd, curve / peak - 1.0)
    stdev = statistics.pstdev(values)
    return {"days": len(values), "mean": statistics.fmean(values), "cagr": curve ** (365.25 / len(values)) - 1.0, "max_drawdown": max_dd, "tail_p05": sorted(values)[max(0, int(0.05 * (len(values) - 1)))], "sharpe": statistics.fmean(values) / stdev * math.sqrt(365.25) if stdev else None}


def _bootstrap(values: list[float]) -> dict[str, float | int | None]:
    if len(values) < 5:
        return {"replicates": 0, "mean_ci_low": None, "mean_ci_high": None}
    rng = random.Random(20260909)
    blocks = [values[i : i + 5] for i in range(0, len(values), 5)]
    samples = []
    for _ in range(256):
        sample = []
        while len(sample) < len(values):
            sample.extend(rng.choice(blocks))
        samples.append(statistics.fmean(sample[: len(values)]))
    samples.sort()
    return {"replicates": len(samples), "mean_ci_low": samples[int(0.025 * len(samples))], "mean_ci_high": samples[int(0.975 * len(samples)) - 1]}


def _correlation(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b) or len(a) < 2 or statistics.pstdev(a) == 0 or statistics.pstdev(b) == 0:
        return None
    return statistics.correlation(a, b)


def run_arm(signal: dict[str, dict[date, float]], prices: dict[str, dict[date, float]], v3: dict[date, float], lag: int, cost_multiplier: int, window: str) -> dict[str, Any]:
    asset_returns = _returns(prices)
    start, end = (START, END) if window == "full" else (date(2024, 1, 1), END) if window == "recent" else (HOLDOUT_START, END)
    dates = [day for day in sorted(v3) if start <= day <= end]
    candidate: list[float] = []
    candidate_dates: list[date] = []
    v3_values: list[float] = []
    prev_weights: dict[str, float] = {asset: 0.0 for asset in prices}
    turnover = 0.0
    active_days = 0
    active_counts: list[int] = []
    for day in dates:
        feature_day = day - timedelta(days=lag)
        active = [asset for asset in prices if signal.get(asset, {}).get(feature_day, 0.0) > 0]
        weights = {asset: 1.0 / len(active) if asset in active else 0.0 for asset in prices} if active else {asset: 0.0 for asset in prices}
        turnover += sum(abs(weights[a] - prev_weights[a]) for a in prices)
        gross = sum(weights[a] * asset_returns[a].get(day, 0.0) for a in prices)
        net = gross - BASE_COST * cost_multiplier * sum(abs(weights[a] - prev_weights[a]) for a in prices)
        candidate.append(net)
        candidate_dates.append(day)
        v3_values.append(v3.get(day, 0.0))
        active_days += bool(active)
        active_counts.append(len(active))
        prev_weights = weights
    metric = _metric(candidate)
    positive_days = sum(value > 0 for value in candidate)
    negative_days = sum(value < 0 for value in candidate)
    down = [r for r, v in zip(candidate, v3_values) if v < 0]
    up = [r for r, v in zip(candidate, v3_values) if v >= 0]
    overlay = [0.95 * v + 0.05 * c for v, c in zip(v3_values, candidate)]
    rng = random.Random(20260910)
    placebo = []
    for _ in range(128):
        shuffled = list(candidate)
        rng.shuffle(shuffled)
        corr = _correlation(shuffled, v3_values)
        if corr is not None:
            placebo.append(abs(corr))
    observed = _correlation(candidate, v3_values)
    position_violations = sum(1 for count in active_counts if count + 6 > 6)
    return {
        "window": window,
        "lag_days": lag,
        "cost_multiplier": cost_multiplier,
        "candidate_metrics": metric,
        "candidate_bootstrap": _bootstrap(candidate),
        "overlay_metrics": _metric(overlay),
        "v3_metrics": _metric(v3_values),
        "overlay_minus_v3_cagr": (_metric(overlay)["cagr"] - _metric(v3_values)["cagr"] if _metric(overlay)["cagr"] is not None and _metric(v3_values)["cagr"] is not None else None),
        "turnover": turnover,
        "active_days": active_days,
        "positive_day_fraction": positive_days / len(candidate) if candidate else 0.0,
        "negative_day_fraction": negative_days / len(candidate) if candidate else 0.0,
        "max_active_candidate_positions": max(active_counts, default=0),
        "regimes": {"v3_down_mean": statistics.fmean(down) if down else None, "v3_up_or_flat_mean": statistics.fmean(up) if up else None},
        "candidate_v3_return_correlation": observed,
        "permutation_placebo": {"replicates": len(placebo), "absolute_corr_pvalue": (1 + sum(value >= abs(observed) for value in placebo)) / (1 + len(placebo)) if observed is not None else None},
        "shared_capital": {"assumed_v3_holdings_for_conservative_check": 6, "violating_days": position_violations, "feasible": position_violations == 0 and max(active_counts, default=0) <= 2},
        "verdict": "RETROSPECTIVE_ONLY_NO_PIT_NO_ELIGIBILITY",
    }


def total_arms(results: dict[str, Any]) -> int:
    return sum(len(row["arms"]) + len(row.get("funding_short_history", {}).get("arms", [])) for row in results.values())


def verdict_grid(results: dict[str, Any]) -> dict[str, Any]:
    grid: dict[str, Any] = {}
    for card, row in results.items():
        card_grid: dict[str, Any] = {}
        for arm in row.get("arms", []):
            card_grid.setdefault(f"lag_{arm['lag_days']}", {}).setdefault(f"cost_{arm['cost_multiplier']}x", {})[arm["window"]] = {
                "verdict": arm["verdict"],
                "cagr": arm["candidate_metrics"]["cagr"],
                "max_drawdown": arm["candidate_metrics"]["max_drawdown"],
                "tail_p05": arm["candidate_metrics"]["tail_p05"],
                "turnover": arm["turnover"],
                "bootstrap": arm["candidate_bootstrap"],
                "permutation_placebo": arm["permutation_placebo"],
                "regimes": arm["regimes"],
                "overlay_cagr": arm["overlay_metrics"]["cagr"],
                "v3_cagr": arm["v3_metrics"]["cagr"],
                "overlay_minus_v3_cagr": arm["overlay_minus_v3_cagr"],
                "shared_capital": arm["shared_capital"],
            }
        if row.get("funding_short_history"):
            funding_grid: dict[str, Any] = {}
            for arm in row["funding_short_history"]["arms"]:
                funding_grid.setdefault(f"lag_{arm['lag_days']}", {}).setdefault(f"cost_{arm['cost_multiplier']}x", {})[arm["window"]] = {
                    "verdict": arm["verdict"],
                    "cagr": arm["candidate_metrics"]["cagr"],
                    "max_drawdown": arm["candidate_metrics"]["max_drawdown"],
                    "tail_p05": arm["candidate_metrics"]["tail_p05"],
                    "turnover": arm["turnover"],
                    "bootstrap": arm["candidate_bootstrap"],
                    "permutation_placebo": arm["permutation_placebo"],
                    "regimes": arm["regimes"],
                    "overlay_cagr": arm["overlay_metrics"]["cagr"],
                    "v3_cagr": arm["v3_metrics"]["cagr"],
                    "overlay_minus_v3_cagr": arm["overlay_minus_v3_cagr"],
                    "shared_capital": arm["shared_capital"],
                }
            card_grid["funding_short_history"] = funding_grid
        grid[card] = card_grid or {"status": row.get("status"), "reason": row.get("reason")}
    return grid


def build() -> dict[str, Any]:
    protocol = freeze_protocol()
    prices = load_prices()
    v3 = load_v3()
    signals: dict[str, dict[str, dict[date, float]]] = {
        "K01": feature_map(K01, "CapMVRVCur"),
        "K03": k03_features(),
        "K04": k04_features(),
        "K11": k11_features(),
    }
    signals["K01"] = {asset: {day: 1.0 if value <= 1.0 else 0.0 for day, value in values.items()} for asset, values in signals["K01"].items()}
    signals["K03"] = {asset: {day: 1.0 if value > 0 else 0.0 for day, value in values.items()} for asset, values in signals["K03"].items()}
    signals["K04"] = {asset: {day: 1.0 if value <= 0.0001 else 0.0 for day, value in values.items()} for asset, values in signals["K04"].items()}
    signals["K11"] = {"btc": {day: 1.0 if value > 0 else 0.0 for day, value in signals["K11"]["btc"].items()}}
    k09_base, k09_funding = k09_features()
    signals["K09"] = {asset: {day: 1.0 if value <= -1.0 else 0.0 for day, value in values.items()} for asset, values in k09_base.items()}
    results: dict[str, Any] = {}
    for card, signal in signals.items():
        arms = [run_arm(signal, prices, v3, lag, cost, window) for lag in LAGS for cost in COST_MULTIPLIERS for window in ("full", "recent", "holdout")]
        results[card] = {"signal_rows": sum(len(values) for values in signal.values()), "arms": arms, "causal_admissible": False, "economic_eligible": False, "admitted": 0, "status": "RETROSPECTIVE_DIAGNOSTIC_ONLY_NO_PIT"}
        if card == "K09":
            results[card]["funding_short_history"] = {
                "signal_rows": sum(len(values) for values in k09_funding.values()),
                "arms": [run_arm({asset: {day: 1.0 if value <= -1.0 else 0.0 for day, value in values.items()} for asset, values in k09_funding.items()}, prices, v3, lag, cost, window) for lag in LAGS for cost in COST_MULTIPLIERS for window in ("full", "recent", "holdout")],
                "status": "RETROSPECTIVE_DIAGNOSTIC_ONLY_NO_PIT",
            }
    for card, reason in {
        "K02": "adjusted-transfer field unavailable under supplied access",
        "K05": "no immutable asset-specific announcement and confirmed transfer rows",
        "K06": "no issuer creation/redemption publication vintage",
        "K07": "mutable labels have no historical PIT vintages",
        "K08": "exact contract/label PIT flow unavailable",
        "K10": "liquidation archive lacks causal availability and OI/event join",
        "K12": "SOPR/entity-adjusted PIT series unavailable",
    }.items():
        results[card] = {"signal_rows": 0, "arms": [], "causal_admissible": False, "economic_eligible": False, "admitted": 0, "status": "NO_DEFENSIBLE_RETROSPECTIVE_DIAGNOSTIC", "reason": reason}
    payload = {
        "schema": "crypto-max-diagnostic-results-v1",
        "protocol_sha256": protocol["protocol_sha256"],
        "reference_manifest_sha256": sha(REFERENCE),
        "v3_artifact_sha256": sha(V3),
        "cards": results,
        "verdict_grid": verdict_grid(results),
        "counts": {"cards": 12, "diagnostic_cards": sum(bool(row["arms"]) for row in results.values()), "economic_run": 0, "admitted": 0, "eligible": 0, "promotion": 0},
        "economic_run": False,
        "admitted": 0,
        "eligible": False,
        "promotion": False,
        "results_sha256": None,
    }
    payload["results_sha256"] = sha(canonical({k: v for k, v in payload.items() if k != "results_sha256"}))
    RESULTS.write_bytes(canonical(payload))
    lines = [
        "# Crypto maximum-testing retrospective diagnostics — 2026-09-09",
        "",
        "This is a deterministic research-only diagnostic. The Coin Metrics reference response and every native feature row lack row-level `available_at`; lagged reconstructions are therefore not causal PIT evidence. No result admits, promotes, or makes an economic eligibility decision.",
        "",
        f"Protocol SHA: `{payload['protocol_sha256']}`  ",
        f"Results SHA: `{payload['results_sha256']}`  ",
        f"Diagnostic arms: `{total_arms(results)}` (lags 1/3/7 × costs 1x/2x/3x × full/recent/holdout).",
        "",
        "| Card | Status | Signal rows | Arms | Economic/admitted |",
        "|---|---|---:|---:|---|",
    ]
    for card in [f"K{i:02d}" for i in range(1, 13)]:
        row = results[card]
        lines.append(f"| {card} | {row['status']} | {row['signal_rows']} | {len(row['arms'])} | false / 0 |")
    lines.extend(["", "Representative full-window result (lag=1, cost=1x; all other grid arms remain machine-readable in `diagnostic_results.json`):"])
    for card in ("K01", "K03", "K04", "K09", "K11"):
        arm = next(arm for arm in results[card]["arms"] if arm["window"] == "full" and arm["lag_days"] == 1 and arm["cost_multiplier"] == 1)
        lines.append(
            f"- {card}: cagr={arm['candidate_metrics']['cagr']!r}, max_drawdown={arm['candidate_metrics']['max_drawdown']!r}, "
            f"tail_p05={arm['candidate_metrics']['tail_p05']!r}, turnover={arm['turnover']!r}, "
            f"positive_day_fraction={arm['positive_day_fraction']!r}, bootstrap_ci=({arm['candidate_bootstrap']['mean_ci_low']!r},{arm['candidate_bootstrap']['mean_ci_high']!r}), "
            f"placebo_p={arm['permutation_placebo']['absolute_corr_pvalue']!r}, v3_down_mean={arm['regimes']['v3_down_mean']!r}, "
            f"overlay_minus_v3_cagr={arm['overlay_minus_v3_cagr']!r}, cap_violating_days={arm['shared_capital']['violating_days']}"
        )
    funding_arm = next(arm for arm in results["K09"]["funding_short_history"]["arms"] if arm["window"] == "full" and arm["lag_days"] == 1 and arm["cost_multiplier"] == 1)
    lines.append(f"- K09 funding-short-history: signal_rows={results['K09']['funding_short_history']['signal_rows']}, cagr={funding_arm['candidate_metrics']['cagr']!r}, max_drawdown={funding_arm['candidate_metrics']['max_drawdown']!r}, active_days={funding_arm['active_days']}, cap_violating_days={funding_arm['shared_capital']['violating_days']}")
    lines.extend([
        "",
        "K09 has a separate short-funding-history branch; it is not merged with the basis/OI branch. K06, K10, and K12 have no defensible retrospective arm because the exact causal fields or PIT join are absent. K02, K05, K07, and K08 remain blocked by the frozen source gates.",
        "",
        "The overlay is fixed at 95% current V3 daily NAV plus 5% candidate diagnostic return. Shared-capital checks conservatively assume six incumbent V3 holdings, so any active candidate position is reported as a position-cap violation; this is a feasibility diagnostic, not an allocation recommendation.",
        "",
    ])
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    receipt = {
        "schema": "crypto-max-diagnostic-validation-receipt-v1",
        "protocol": {"path": str(PROTOCOL.relative_to(REPO)), "sha256": sha(PROTOCOL)},
        "results": {"path": str(RESULTS.relative_to(REPO)), "sha256": sha(RESULTS)},
        "report": {"path": str(REPORT.relative_to(REPO)), "sha256": sha(REPORT)},
        "reference": {"path": str(REFERENCE.relative_to(REPO)), "sha256": sha(REFERENCE)},
        "v3": {"path": str(V3.relative_to(REPO)), "sha256": sha(V3)},
        "runner": {"path": str(Path(__file__).relative_to(REPO)), "sha256": sha(Path(__file__))},
        "arms": total_arms(results),
        "cards": 12,
        "economic_run": False,
        "network_used": False,
        "admitted": 0,
        "eligible": False,
        "promotion": False,
    }
    RECEIPT.write_bytes(canonical(receipt))
    return {"status": "CRYPTO_RETROSPECTIVE_DIAGNOSTICS_COMPLETE_NO_PIT_ADMISSION", "cards": 12, "diagnostic_cards": payload["counts"]["diagnostic_cards"], "arms": receipt["arms"], "results_sha256": payload["results_sha256"]}


def verify(protocol_path: Path = PROTOCOL, result_path: Path = RESULTS, receipt_path: Path = RECEIPT) -> dict[str, Any]:
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol.get("protocol_sha256") != sha(canonical({k: v for k, v in protocol.items() if k != "protocol_sha256"})):
        raise ValueError("protocol_hash_mismatch")
    for source in protocol.get("feature_sources", []):
        source_path = REPO / source["path"]
        if not source_path.is_file() or sha(source_path) != source.get("sha256"):
            raise ValueError(f"protocol_source_hash_mismatch:{source.get('path')}")
    reference_source = protocol.get("reference_source")
    if not isinstance(reference_source, dict):
        raise ValueError("reference_source_missing")
    reference_path = REPO / reference_source["path"]
    if not reference_path.is_file() or sha(reference_path) != reference_source.get("sha256"):
        raise ValueError(f"protocol_source_hash_mismatch:{reference_source.get('path')}")
    results = json.loads(result_path.read_text(encoding="utf-8"))
    if results.get("results_sha256") != sha(canonical({k: v for k, v in results.items() if k != "results_sha256"})):
        raise ValueError("results_hash_mismatch")
    if results.get("protocol_sha256") != protocol.get("protocol_sha256") or results.get("counts", {}).get("cards") != 12:
        raise ValueError("diagnostic_binding_mismatch")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    for key, path in (("protocol", PROTOCOL), ("results", RESULTS), ("reference", REFERENCE), ("v3", V3)):
        if receipt.get(key, {}).get("sha256") != sha(path):
            raise ValueError(f"receipt_hash_mismatch:{key}")
    if receipt.get("report", {}).get("sha256") != sha(REPORT):
        raise ValueError("receipt_hash_mismatch:report")
    runner = receipt.get("runner")
    if not isinstance(runner, dict) or runner.get("path") != str(Path(__file__).relative_to(REPO)) or runner.get("sha256") != sha(Path(__file__)):
        raise ValueError("receipt_runner_hash_mismatch")
    report = REPORT.read_text(encoding="utf-8")
    if results["results_sha256"] not in report or "Economic/admitted" not in report:
        raise ValueError("report_binding_mismatch")
    if results.get("economic_run") is not False or results.get("admitted") != 0 or results.get("eligible") is not False or results.get("promotion") is not False:
        raise ValueError("unsafe_diagnostic_flags")
    for card, row in results["cards"].items():
        if row.get("causal_admissible") is not False or row.get("economic_eligible") is not False or row.get("admitted") != 0:
            raise ValueError(f"unsafe_card_flags:{card}")
    return {"status": "CRYPTO_RETROSPECTIVE_DIAGNOSTICS_VERIFIED", "cards": 12, "arms": total_arms(results["cards"]), "read_only": True, "network_free": True, "results_sha256": results["results_sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify() if args.verify else build(), sort_keys=True))


if __name__ == "__main__":
    main()
