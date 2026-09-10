"""Run frozen retrospective X01/X05/X06 overlay diagnostics only."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import statistics
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PROTOCOL = REPO / "data/research_manifests/crossasset_max_testing_protocol_2026_09_09_v3.json"
OUT = REPO / "data/research_runs/crossasset_max_testing_2026_09_09_v3"
RESULTS = OUT / "overlay_diagnostic_results.json"
REPORT = OUT / "overlay_diagnostic_report.md"
MANIFEST = OUT / "manifest.json"
RECEIPT = OUT / "validation_receipt.json"
CUTOFF = date(2026, 7, 31)
START = date(2022, 1, 1)
RECENT = date(2024, 1, 1)
HOLDOUT = date(2025, 1, 1)
LAGS = (1, 3, 7)
COSTS = (1, 2, 3)
BASE_COST = 0.001
MAX_EQUITY = 0.55
MAX_CRYPTO = 0.05
MAX_GROSS = 0.60
MIN_CASH = 0.40
MAX_POSITIONS = 6
MAX_CRYPTO_POSITIONS = 2


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def protocol(path: Path = PROTOCOL) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    expected = value.get("protocol_sha256")
    actual = hashlib.sha256(canonical({key: item for key, item in value.items() if key != "protocol_sha256"})).hexdigest()
    if expected != actual or value.get("frozen_before_outcomes") is not True:
        raise ValueError("protocol_hash_or_freeze_mismatch")
    if value.get("lags_days") != [1, 3, 7] or value.get("cost_multipliers") != [1, 2, 3]:
        raise ValueError("protocol_grid_mismatch")
    for source in value["source_inputs"]:
        path = REPO / source["path"]
        if not path.is_file() or sha(path) != source["sha256"]:
            raise ValueError(f"source_hash_mismatch:{source['path']}")
    return value


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def day(value: str) -> date:
    return date.fromisoformat(value[:10])


def finite(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non_finite")
    return result


def load_prices() -> dict[str, dict[date, float]]:
    result: dict[str, dict[date, float]] = {}
    rows = read_csv(REPO / "data/research_runs/crypto_max_testing_2026_09_09/routes/coinmetrics_btc_eth_priceusd_1d_2022_2026-07-31.csv")
    previous: dict[str, date] = {}
    for row in rows:
        asset = row["asset"]
        if asset not in {"btc", "eth"} or row["available_at"]:
            raise ValueError("price_identity_or_availability")
        current = day(row["event_time"])
        if current > CUTOFF or asset in previous and current <= previous[asset]:
            raise ValueError("price_order_or_cutoff")
        result.setdefault(asset, {})[current] = finite(row["price_usd"])
        previous[asset] = current
    if {key: len(value) for key, value in result.items()} != {"btc": 1673, "eth": 1673}:
        raise ValueError("price_count_mismatch")
    return result


def load_etf_prices() -> dict[str, dict[date, float]]:
    result: dict[str, dict[date, float]] = {}
    for symbol in ("SHY", "IEF", "TLT"):
        rows = read_csv(REPO / f"data/research/external/crossasset_public_2026_09_08/x01_yahoo/{symbol}_bars.csv")
        if len(rows) != 2659:
            raise ValueError(f"etf_count_mismatch:{symbol}")
        dates: dict[date, float] = {}
        for row in rows:
            current = day(row["date"])
            if current > CUTOFF or row["symbol"] != symbol:
                raise ValueError(f"etf_identity_or_cutoff:{symbol}")
            close = finite(row["adjclose"])
            if close <= 0:
                raise ValueError(f"etf_price_invalid:{symbol}")
            dates[current] = close
        result[symbol] = dates
    return result


def x01_signal() -> tuple[dict[str, dict[date, float]], dict[str, dict[date, float]]]:
    prices = load_etf_prices()
    rows = read_csv(REPO / "data/research/generated/crossasset_preflight_2026_09_08/x01_treasury_curve_normalized.csv")
    signals: dict[str, dict[date, float]] = {"SHY": {}, "IEF": {}, "TLT": {}}
    for row in rows:
        current = day(row["date"])
        if current > CUTOFF:
            raise ValueError("curve_post_cutoff")
        two_year = finite(row["2y"])
        ten_year = finite(row["10y"])
        slope = (ten_year - two_year) / max(abs(two_year), 1e-9)
        selected = "SHY" if slope > 0 else "TLT" if slope < 0 else "IEF"
        signals[selected][current] = 1.0
    return signals, prices


def x05_signal() -> tuple[dict[str, dict[date, float]], dict[str, dict[date, float]], int, int]:
    rows = read_csv(REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K09_binance_oi_metrics_archive_pilot.csv")
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row["venue"] != "binance" or row["contract_type"] != "USD-M_PERPETUAL" or row["metric"] != "sum_open_interest" or row["available_at"] or row["publication_time"]:
            raise ValueError("oi_identity_or_availability")
        stamp = row["event_time_utc"]
        if day(stamp) > CUTOFF:
            raise ValueError("oi_post_cutoff")
        grouped[stamp][row["instrument"]].append(math.log(max(finite(row["sum_open_interest_value_usd"]), 1e-12)))
    signals = {"btc": {}, "eth": {}}
    signal_rows = 0
    for stamp, assets in sorted(grouped.items()):
        if set(assets) != {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("oi_timestamp_asset_join")
        values = {asset: statistics.fmean(series) for asset, series in assets.items()}
        mean = statistics.fmean(values.values())
        stdev = statistics.pstdev(values.values())
        if stdev == 0:
            continue
        current = day(stamp)
        for asset, value in values.items():
            key = "btc" if asset == "BTCUSDT" else "eth"
            signals[key][current] = signals[key].get(current, 0.0) + (-(value - mean) / stdev)
        signal_rows += 1
    for key in signals:
        counts = sum(1 for value in signals[key].values() if value > 0)
        if counts:
            signals[key] = {current: value / 288.0 for current, value in signals[key].items()}
    return signals, load_prices(), signal_rows, len(rows)


def x06_signal() -> tuple[dict[str, dict[date, float]], dict[str, dict[date, float]], int, int]:
    rows = read_csv(REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K09_binance_funding_archive_pilot.csv")
    grouped: dict[date, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row["venue"] != "binance" or row["contract_type"] != "USD-M_PERPETUAL" or row["metric"] != "funding_rate" or row["unit"] != "fraction_per_interval" or row["available_at"] or row["publication_time"]:
            raise ValueError("funding_identity_or_availability")
        current = day(row["event_time_utc"])
        if current > CUTOFF:
            raise ValueError("funding_post_cutoff")
        grouped[current][row["instrument"]].append(finite(row["value"]))
    signals = {"btc": {}, "eth": {}}
    signal_rows = 0
    for current, assets in sorted(grouped.items()):
        if set(assets) != {"BTCUSDT", "ETHUSDT"}:
            continue
        values = {asset: statistics.fmean(series) for asset, series in assets.items()}
        mean = statistics.fmean(values.values())
        stdev = statistics.pstdev(values.values())
        if stdev == 0:
            continue
        for asset, value in values.items():
            key = "btc" if asset == "BTCUSDT" else "eth"
            signals[key][current] = -(value - mean) / stdev
        signal_rows += 1
    return signals, load_prices(), signal_rows, len(rows)


def v3_context() -> tuple[dict[date, float], dict[date, dict[str, float]]]:
    value = json.loads((REPO / "data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/current_v3_daily_nav_comparability.json").read_text(encoding="utf-8"))
    nav = {day(row["date"]): float(row["nav"]) for row in value["daily_nav"]}
    ordered = sorted(nav)
    returns = {current: nav[current] / nav[previous] - 1.0 for previous, current in zip(ordered, ordered[1:])}
    history = sorted((datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")), {key: float(weight) for key, weight in row["weights"].items()}) for row in value["weight_history"])
    weights: dict[date, dict[str, float]] = {}
    latest: dict[str, float] = {}
    index = 0
    for current in sorted(returns):
        timestamp = datetime.combine(current, datetime.min.time(), tzinfo=history[0][0].tzinfo) if history else None
        while timestamp is not None and index < len(history) and history[index][0] <= timestamp:
            latest = history[index][1]
            index += 1
        weights[current] = latest
    return returns, weights


def metric(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"days": 0, "mean": None, "cagr": None, "max_drawdown": None, "cvar_p05": None, "sharpe": None}
    curve = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in values:
        curve *= 1.0 + value
        peak = max(peak, curve)
        max_drawdown = min(max_drawdown, curve / peak - 1.0)
    tail = sorted(values)[: max(1, int(len(values) * 0.05))]
    stdev = statistics.pstdev(values)
    return {"days": len(values), "mean": statistics.fmean(values), "cagr": curve ** (365.25 / len(values)) - 1.0, "max_drawdown": max_drawdown, "cvar_p05": statistics.fmean(tail), "sharpe": statistics.fmean(values) / stdev * math.sqrt(365.25) if stdev else None}


def bootstrap(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"replicates": 256, "mean_ci_low": None, "mean_ci_high": None}
    rng = random.Random(20260909)
    block = 5
    means = []
    for _ in range(256):
        sample: list[float] = []
        while len(sample) < len(values):
            start = rng.randrange(len(values))
            sample.extend(values[(start + offset) % len(values)] for offset in range(block))
        means.append(statistics.fmean(sample[: len(values)]))
    means.sort()
    return {"replicates": 256, "mean_ci_low": means[6], "mean_ci_high": means[249]}


def correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
    left_sd = math.sqrt(sum((a - left_mean) ** 2 for a in left))
    right_sd = math.sqrt(sum((b - right_mean) ** 2 for b in right))
    return numerator / (left_sd * right_sd) if left_sd and right_sd else None


def run_arm(signal: dict[str, dict[date, float]], prices: dict[str, dict[date, float]], v3: dict[date, float], weights: dict[date, dict[str, float]], card: str, lag: int, cost: int, window: str, mode: str) -> dict[str, Any]:
    start = START if window == "full" else RECENT if window == "recent" else HOLDOUT
    dates = [current for current in sorted(v3) if start <= current <= CUTOFF]
    candidate_returns: dict[str, dict[date, float]] = {}
    for asset, series in prices.items():
        ordered = sorted(series)
        candidate_returns[asset] = {current: series[next_day] / series[current] - 1.0 for current, next_day in zip(ordered, ordered[1:]) if current < CUTOFF}
    portfolio: list[float] = []
    standalone: list[float] = []
    contribution: list[float] = []
    incumbent: list[float] = []
    turnovers = 0.0
    previous_candidate: dict[str, float] = {}
    active_days = 0
    position_violations = 0
    equity_violations = 0
    crypto_violations = 0
    gross_violations = 0
    cash_violations = 0
    for current in dates:
        prior = current - timedelta(days=lag)
        active = sorted(asset for asset, series in signal.items() if series.get(prior, 0.0) > 0 and current in candidate_returns.get(asset, {}))
        v3_weights = weights.get(current, {})
        holdings = len(v3_weights)
        exposure = sum(v3_weights.values())
        if mode == "fixed_95_5":
            sleeve = 0.05
            incumbent_factor = 0.95
        else:
            class_headroom = MAX_CRYPTO if card in {"X05", "X06"} else max(0.0, MAX_EQUITY - exposure)
            spare = min(class_headroom, MAX_GROSS - exposure, 1.0 - MIN_CASH - exposure)
            sleeve = max(0.0, spare) if holdings < MAX_POSITIONS else 0.0
            incumbent_factor = 1.0
            active = active[: max(0, min(MAX_CRYPTO_POSITIONS, MAX_POSITIONS - holdings))]
        candidate = {asset: sleeve / len(active) if active else 0.0 for asset in active}
        turnover = sum(abs(candidate.get(asset, 0.0) - previous_candidate.get(asset, 0.0)) for asset in prices)
        standalone_return = statistics.fmean(candidate_returns[asset][current] for asset in active) if active else 0.0
        candidate_return = sum(candidate.get(asset, 0.0) * candidate_returns[asset].get(current, 0.0) for asset in prices) - BASE_COST * cost * turnover
        v3_return = v3[current]
        contribution.append(candidate_return)
        standalone.append(standalone_return)
        incumbent.append(v3_return)
        portfolio.append(incumbent_factor * v3_return + candidate_return)
        turnovers += turnover
        active_days += bool(active)
        total_exposure = incumbent_factor * exposure + sleeve
        if holdings + len(active) > MAX_POSITIONS:
            position_violations += 1
        if card == "X01" and total_exposure > MAX_EQUITY + 1e-12:
            equity_violations += 1
        if card in {"X05", "X06"} and len(active) > MAX_CRYPTO_POSITIONS:
            crypto_violations += 1
        if total_exposure > MAX_GROSS + 1e-12:
            gross_violations += 1
        if 1.0 - total_exposure < MIN_CASH - 1e-12:
            cash_violations += 1
        previous_candidate = candidate
    placebo: list[float] = []
    rng = random.Random(20260910)
    observed = correlation(contribution, incumbent)
    for _ in range(128):
        shuffled = list(contribution)
        rng.shuffle(shuffled)
        value = correlation(shuffled, incumbent)
        if value is not None:
            placebo.append(abs(value))
    down = [value for value, base_value in zip(contribution, incumbent, strict=True) if base_value < 0]
    up = [value for value, base_value in zip(contribution, incumbent, strict=True) if base_value >= 0]
    return {"mode": mode, "card": card, "window": window, "lag_days": lag, "cost_multiplier": cost, "active_days": active_days, "active_day_fraction": active_days / len(dates) if dates else 0.0, "turnover": turnovers, "standalone_metrics": metric(standalone), "standalone_bootstrap": bootstrap(standalone), "portfolio_metrics": metric(portfolio), "candidate_metrics": metric(contribution), "bootstrap": bootstrap(contribution), "regimes": {"v3_down_mean_contribution": statistics.fmean(down) if down else None, "v3_up_or_flat_mean_contribution": statistics.fmean(up) if up else None}, "correlation": observed, "permutation_placebo": {"replicates": len(placebo), "absolute_corr_pvalue": (1 + sum(value >= abs(observed) for value in placebo)) / (1 + len(placebo)) if observed is not None else None}, "caps": {"position_violations": position_violations, "equity_violations": equity_violations, "crypto_violations": crypto_violations, "gross_violations": gross_violations, "cash_violations": cash_violations}, "status": "RETROSPECTIVE_NON_PIT_DIAGNOSTIC", "causal_admissible": False, "economic_eligible": False, "admitted_rows": 0}


def build() -> dict[str, Any]:
    frozen = protocol()
    v3, weights = v3_context()
    signals: dict[str, tuple[dict[str, dict[date, float]], dict[str, dict[date, float]], int, int]] = {}
    x01, x01_prices = x01_signal()
    signals["X01"] = (x01, x01_prices, sum(len(rows) for rows in x01.values()), sum(len(series) for series in x01_prices.values()))
    signals["X05"] = (*x05_signal(),)
    signals["X06"] = (*x06_signal(),)
    cards: dict[str, Any] = {}
    for card, (signal, prices, signal_rows, raw_rows) in signals.items():
        arms = [run_arm(signal, prices, v3, weights, card, lag, cost, window, mode) for mode in ("fixed_95_5", "capacity_aware") for lag in LAGS for cost in COSTS for window in ("full", "recent", "true_holdout")]
        cards[card] = {"signal_rows": signal_rows, "raw_evidence_rows": raw_rows, "curve_rows": signal_rows if card == "X01" else None, "bars_by_asset": {asset: len(series) for asset, series in sorted(prices.items())} if card == "X01" else {}, "outcome_price_assets": sorted(prices), "arms": arms, "status": "RETROSPECTIVE_NON_PIT_DIAGNOSTIC", "causal_admissible": False, "economic_eligible": False, "admitted_rows": 0, "available_at_rows": 0}
    gate = json.loads((REPO / "data/research_runs/crossasset_x02_x06_2026_09_08/gate_report.json").read_text(encoding="utf-8"))
    for card in ("X02", "X03", "X04"):
        cards[card] = {"signal_rows": 0, "arms": [], "status": gate[card]["status"], "blocker": gate[card].get("binding_status") or gate[card].get("required_fields_missing") or gate[card].get("status"), "causal_admissible": False, "economic_eligible": False, "admitted_rows": 0}
    payload: dict[str, Any] = {"schema": "crossasset-max-testing-results-v3", "protocol_sha256": frozen["protocol_sha256"], "cards": cards, "counts": {"cards": 6, "measured_cards": 3, "arms": sum(len(row["arms"]) for row in cards.values()), "economic_run": 0, "admitted_rows": 0, "economic_eligible": 0, "promotion": 0}, "overlay_status": "RETROSPECTIVE_ONLY_NO_PIT_ADMISSION", "economic_run": False, "admitted_rows": 0, "economic_eligible": False, "promotion": False, "results_sha256": None}
    payload["results_sha256"] = hashlib.sha256(canonical({key: value for key, value in payload.items() if key != "results_sha256"})).hexdigest()
    result_bytes = canonical(payload)
    report_lines = ["# Crossasset maximum-testing V3 diagnostics — 2026-09-09", "", "Fixed formulas and grids were frozen before outcomes. Coin Metrics PriceUSD and the local Yahoo ETF bars are retrospective outcomes only; all source rows lack `available_at`, so admission and economic eligibility remain false.", "", f"Protocol SHA: `{frozen['protocol_sha256']}`  ", f"Results canonical SHA: `{payload['results_sha256']}`  ", f"Arms: `{payload['counts']['arms']}` (3 measured cards × 2 overlay modes × 3 lags × 3 costs × 3 windows).", "", "| Card | Signal rows | Fixed/capacity arms | Status |", "|---|---:|---:|---|"]
    for card in ("X01", "X02", "X03", "X04", "X05", "X06"):
        row = cards[card]
        report_lines.append(f"| {card} | {row['signal_rows']} | {len(row['arms'])} | {row['status']} |")
    report_lines.extend(["", "Capacity-aware arms preserve V3 and allocate only spare capacity; fixed 95/5 arms report cap violations where present. No arm changes V3, admission, promotion, or production state.", ""])
    report_bytes = "\n".join(report_lines).encode()
    manifest: dict[str, Any] = {"schema": "crossasset-max-testing-manifest-v3", "protocol": {"path": str(PROTOCOL.relative_to(REPO)), "sha256": sha(PROTOCOL)}, "inputs": [{"path": source["path"], "sha256": source["sha256"]} for source in frozen["source_inputs"]], "artifacts": {str(RESULTS.relative_to(REPO)): {"sha256": hashlib.sha256(result_bytes).hexdigest(), "bytes": len(result_bytes)}, str(REPORT.relative_to(REPO)): {"sha256": hashlib.sha256(report_bytes).hexdigest(), "bytes": len(report_bytes)}}, "economic_run": False, "admitted_rows": 0, "manifest_sha256": "PENDING_FREEZE"}
    manifest["manifest_sha256"] = hashlib.sha256(canonical({key: value for key, value in manifest.items() if key != "manifest_sha256"})).hexdigest()
    manifest_bytes = canonical(manifest)
    receipt: dict[str, Any] = {"schema": "crossasset-max-testing-receipt-v3", "protocol": {"path": str(PROTOCOL.relative_to(REPO)), "sha256": sha(PROTOCOL)}, "results": {"path": str(RESULTS.relative_to(REPO)), "sha256": hashlib.sha256(result_bytes).hexdigest()}, "report": {"path": str(REPORT.relative_to(REPO)), "sha256": hashlib.sha256(report_bytes).hexdigest()}, "manifest": {"path": str(MANIFEST.relative_to(REPO)), "sha256": hashlib.sha256(manifest_bytes).hexdigest()}, "cards": 6, "measured_cards": 3, "arms": payload["counts"]["arms"], "economic_run": False, "admitted_rows": 0, "economic_eligible": False, "promotion": False, "network_used": False, "receipt_sha256": "PENDING_FREEZE"}
    receipt["receipt_sha256"] = hashlib.sha256(canonical({key: value for key, value in receipt.items() if key != "receipt_sha256"})).hexdigest()
    atomic_write(RESULTS, result_bytes)
    atomic_write(REPORT, report_bytes)
    atomic_write(MANIFEST, manifest_bytes)
    atomic_write(RECEIPT, canonical(receipt))
    return {"status": "CROSSASSET_MAX_TESTING_COMPLETE_V3", "cards": 6, "measured_cards": 3, "arms": payload["counts"]["arms"], "admitted_rows": 0, "results_sha256": payload["results_sha256"], "manifest_sha256": manifest["manifest_sha256"], "receipt_sha256": receipt["receipt_sha256"]}


def verify() -> dict[str, Any]:
    frozen = protocol()
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    if payload.get("results_sha256") != hashlib.sha256(canonical({key: value for key, value in payload.items() if key != "results_sha256"})).hexdigest() or payload.get("protocol_sha256") != frozen["protocol_sha256"]:
        raise ValueError("results_hash_mismatch")
    if payload.get("counts") != {"cards": 6, "measured_cards": 3, "arms": 162, "economic_run": 0, "admitted_rows": 0, "economic_eligible": 0, "promotion": 0}:
        raise ValueError("results_count_mismatch")
    for card, row in payload["cards"].items():
        if row.get("economic_eligible") is not False or row.get("admitted_rows") != 0 or row.get("causal_admissible") is not False:
            raise ValueError(f"unsafe_card:{card}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("manifest_sha256") != hashlib.sha256(canonical({key: value for key, value in manifest.items() if key != "manifest_sha256"})).hexdigest():
        raise ValueError("manifest_hash_mismatch")
    if manifest["artifacts"][str(RESULTS.relative_to(REPO))]["sha256"] != sha(RESULTS) or manifest["artifacts"][str(REPORT.relative_to(REPO))]["sha256"] != sha(REPORT):
        raise ValueError("manifest_artifact_mismatch")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("receipt_sha256") != hashlib.sha256(canonical({key: value for key, value in receipt.items() if key != "receipt_sha256"})).hexdigest():
        raise ValueError("receipt_hash_mismatch:self")
    for key, path in (("protocol", PROTOCOL), ("results", RESULTS), ("report", REPORT), ("manifest", MANIFEST)):
        if receipt.get(key, {}).get("sha256") != sha(path):
            raise ValueError(f"receipt_hash_mismatch:{key}")
    return {"status": "CROSSASSET_MAX_TESTING_V3_VERIFIED", "cards": 6, "measured_cards": 3, "arms": 162, "admitted_rows": 0, "economic_eligible": False, "read_only": True, "network_free": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify() if args.verify else build(), sort_keys=True))


if __name__ == "__main__":
    main()
