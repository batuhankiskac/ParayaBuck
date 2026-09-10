"""Frozen, descriptive crossasset diagnostics with no economic admission path."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PROTOCOL = REPO / "data/research_manifests/crossasset_protocol_amendment_2026_09_09_v2.json"
OUT = REPO / "data/research_runs/crossasset_retrospective_diagnostics_2026_09_09"
RESULTS = OUT / "diagnostic_results.json"
REPORT = OUT / "diagnostic_report.md"
MANIFEST = OUT / "manifest.json"
RECEIPT = OUT / "validation_receipt.json"
CUTOFF = date(2026, 7, 31)
RECENT = date(2024, 1, 1)
HOLDOUT = date(2025, 1, 1)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def parse_day(value: str) -> date:
    return date.fromisoformat(value[:10].replace("/", "-"))


def finite(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non_finite")
    return parsed


def _protocol(path: Path = PROTOCOL) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = payload.get("protocol_sha256")
    actual = hashlib.sha256(canonical({key: value for key, value in payload.items() if key != "protocol_sha256"})).hexdigest()
    if expected != actual:
        raise ValueError("protocol_hash_mismatch")
    if payload.get("frozen_before_outcomes") is not True or payload.get("lags_days") != [1, 3, 7]:
        raise ValueError("protocol_not_frozen")
    if payload.get("economic_run") is not False or payload.get("admitted_rows") != 0 or payload.get("economic_eligible") is not False or payload.get("promotion") is not False:
        raise ValueError("unsafe_protocol_flags")
    return payload


def _verified_sources(protocol: dict[str, Any]) -> None:
    for source in protocol["source_inputs"]:
        path = REPO / source["path"]
        if not path.is_file() or sha256(path) != source["sha256"]:
            raise ValueError(f"protocol_source_hash_mismatch:{source['path']}")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def validate_curve_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    required = {"date", "2y", "10y"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("curve_schema")
    result: list[dict[str, Any]] = []
    previous: date | None = None
    for row in rows:
        day = parse_day(row["date"])
        if day > CUTOFF:
            raise ValueError("post_cutoff")
        if previous is not None and day <= previous:
            raise ValueError("curve_order")
        two_year = finite(row["2y"])
        ten_year = finite(row["10y"])
        if two_year == 0.0 and ten_year == 0.0:
            slope = 0.0
        else:
            slope = (ten_year - two_year) / max(abs(two_year), 1e-9)
        result.append({"date": day, "signal": slope})
        previous = day
    return result


def _validate_short_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    required = {"date", "symbol", "short_volume", "total_volume", "short_fraction"}
    if not rows or set(rows[0]) != required:
        raise ValueError("short_volume_schema")
    result: list[dict[str, Any]] = []
    for row in rows:
        day = parse_day(row["date"])
        short = int(row["short_volume"])
        total = int(row["total_volume"])
        fraction = finite(row["short_fraction"])
        if day > CUTOFF or short < 0 or total <= 0 or abs(fraction - short / total) > 1e-12:
            raise ValueError("short_volume_invalid")
        result.append({"date": day, "symbol": row["symbol"], "signal": fraction - 0.5})
    return result


def _validate_funding_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    required = {"venue", "contract_type", "instrument", "event_time_utc", "metric", "value", "unit", "funding_interval_hours", "source_file", "source_sha256", "available_at", "publication_time"}
    if not rows or set(rows[0]) != required:
        raise ValueError("funding_schema")
    result: list[dict[str, Any]] = []
    previous: dict[str, datetime] = {}
    for row in rows:
        if row["venue"] != "binance" or row["contract_type"] != "USD-M_PERPETUAL" or row["metric"] != "funding_rate" or row["unit"] != "fraction_per_interval" or int(row["funding_interval_hours"]) != 8:
            raise ValueError("funding_identity")
        event = datetime.fromisoformat(row["event_time_utc"].replace("Z", "+00:00"))
        if event.tzinfo is None or event.date() > CUTOFF or row["available_at"] or row["publication_time"]:
            raise ValueError("funding_pit_or_timestamp")
        if row["instrument"] in previous and event <= previous[row["instrument"]]:
            raise ValueError("funding_order")
        previous[row["instrument"]] = event
        result.append({"date": event.date(), "instrument": row["instrument"], "signal": finite(row["value"])})
    return result


def _validate_bar_rows(rows: list[dict[str, str]]) -> None:
    required = {"venue", "instrument", "bar_kind", "event_time_utc", "open", "high", "low", "close", "close_time_utc", "source_file", "source_sha256", "available_at"}
    if not rows or set(rows[0]) != required:
        raise ValueError("bar_schema")
    previous: dict[tuple[str, str], datetime] = {}
    for row in rows:
        event = datetime.fromisoformat(row["event_time_utc"].replace("Z", "+00:00"))
        if event.tzinfo is None or event.date() > CUTOFF or row["venue"] != "binance" or not row["instrument"] or not row["bar_kind"] or row["available_at"]:
            raise ValueError("bar_identity_or_pit")
        key = (row["instrument"], row["bar_kind"])
        if key in previous and event <= previous[key]:
            raise ValueError("bar_order")
        prices = [finite(row[field]) for field in ("open", "high", "low", "close")]
        if any(value <= 0 for value in prices) or prices[2] > min(prices[0], prices[3]) or prices[1] < max(prices[0], prices[3]):
            raise ValueError("bar_ohlc")
        previous[key] = event


def _stats(rows: list[dict[str, Any]], *, group_key: str | None = None) -> dict[str, Any]:
    if not rows:
        return {"rows": 0, "mean": None, "min": None, "max": None, "positive_fraction": None, "groups": {}}
    values = [row["signal"] for row in rows]
    groups: dict[str, list[float]] = defaultdict(list)
    if group_key:
        for row in rows:
            groups[str(row[group_key])].append(row["signal"])
    return {
        "rows": len(rows),
        "mean": sum(values) / len(values),
        "min": min(values),
        "max": max(values),
        "positive_fraction": sum(value > 0 for value in values) / len(values),
        "groups": {key: _stats([{"signal": value} for value in values]) for key, values in sorted(groups.items())} if group_key else {},
    }


def _diagnostic_branch(rows: list[dict[str, Any]], *, group_key: str | None = None) -> dict[str, Any]:
    windows = {
        "full": rows,
        "recent": [row for row in rows if row["date"] >= RECENT],
        "holdout": [row for row in rows if row["date"] >= HOLDOUT],
    }
    lagged = {str(lag): sum(row["date"] + timedelta(days=lag) <= CUTOFF for row in rows) for lag in (1, 3, 7)}
    return {
        "signal_rows": len(rows),
        "lagged_rows": lagged,
        "windows": {name: _stats(part, group_key=group_key) for name, part in windows.items()},
        "metric_disposition": {
            "standalone_return": "NOT_RUN_MISSING_CANDIDATE_RETURN",
            "turnover": "NOT_RUN_MISSING_EXECUTABLE_RETURN",
            "cost_1x_2x_3x": "NOT_RUN_MISSING_EXECUTABLE_RETURN",
            "drawdown_tail": "NOT_RUN_MISSING_CANDIDATE_RETURN",
            "correlation": "NOT_RUN_MISSING_CANDIDATE_RETURN",
            "regime_analysis": "NOT_RUN_MISSING_DECLARED_REGIME_LABELS",
            "bootstrap": "NOT_RUN_MISSING_OUTCOME",
            "permutation_placebo": "NOT_RUN_MISSING_OUTCOME",
            "two_leg_feasibility": "BLOCKED_MISSING_BORROW_MARGIN_OR_EXECUTION",
        },
        "status": "RETROSPECTIVE_NON_PIT_DIAGNOSTIC",
        "causal_admissible": False,
        "economic_eligible": False,
        "admitted_rows": 0,
    }


def _blocked(card: str, source: dict[str, Any]) -> dict[str, Any]:
    row = source.get(card, {})
    counts = {key: row[key] for key in ("raw_rows", "unique_rows", "unique_event_keys", "exact_duplicate_rows", "structured_evidence_rows", "identity_bound_structured_evidence_rows") if key in row}
    blocker = row.get("economic_gate") or row.get("binding_status") or row.get("required_fields_missing") or row.get("outcome") or row.get("status") or "required causal field unavailable"
    return {
        "status": row.get("status", "DATA_BLOCKED_REQUIRED_CAUSAL_FIELD_MISSING"),
        "blocker": blocker,
        "evidence_counts": counts,
        "signal_rows": 0,
        "lagged_rows": {"1": 0, "3": 0, "7": 0},
        "windows": {},
        "metric_disposition": "NOT_RUN_INPUT_GATE_BLOCKED",
        "causal_admissible": False,
        "economic_eligible": False,
        "admitted_rows": 0,
    }


def _load_blocker_sources() -> tuple[dict[str, Any], dict[str, Any]]:
    preflight = json.loads((REPO / "data/research_manifests/crossasset_preflight_2026_09_08.json").read_text(encoding="utf-8"))
    gate = json.loads((REPO / "data/research_runs/crossasset_x02_x06_2026_09_08/gate_report.json").read_text(encoding="utf-8"))
    return preflight, gate


def build() -> dict[str, Any]:
    protocol = _protocol()
    _verified_sources(protocol)
    preflight, gate = _load_blocker_sources()
    curve = validate_curve_rows(_read_csv(REPO / "data/research/generated/crossasset_preflight_2026_09_08/x01_treasury_curve_normalized.csv"))
    short = _validate_short_rows(_read_csv(REPO / "data/research/generated/crossasset_preflight_2026_09_08/x04_pairs_finra_short_volume_sample.csv"))
    funding = _validate_funding_rows(_read_csv(REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K09_binance_funding_archive_pilot.csv"))
    _validate_bar_rows(_read_csv(REPO / "data/research/external/crypto_queue_acquisition_2026_09_07/normalized/K09_binance_causal_bars_pilot.csv"))
    cards = {
        "X01": {**_diagnostic_branch(curve), "formula": protocol["formulae"]["X01"], "blockers": preflight["known_blockers"]["X01"]},
        "X02": _blocked("X02", gate),
        "X03": _blocked("X03", gate),
        "X04": {**_diagnostic_branch(short, group_key="symbol"), "formula": protocol["formulae"]["X04"], "blockers": preflight["known_blockers"]["X04"], "lower_bound_only": True},
        "X05": _blocked("X05", gate),
        "X06": {**_diagnostic_branch(funding, group_key="instrument"), "formula": protocol["formulae"]["X06"], "blockers": preflight["known_blockers"]["X06"], "availability_rows": 0},
    }
    payload: dict[str, Any] = {
        "schema": "crossasset-retrospective-diagnostic-results-v2",
        "protocol_sha256": protocol["protocol_sha256"],
        "cutoff": protocol["cutoff"],
        "cards": cards,
        "overlay_status": protocol["overlay"]["status"],
        "constraints": protocol["overlay"]["constraints"],
        "counts": {"cards": 6, "diagnostic_cards": 3, "admitted_rows": 0, "economic_run": 0, "economic_eligible": 0, "promotion": 0},
        "economic_run": False,
        "economic_eligible": False,
        "admitted_rows": 0,
        "promotion": False,
        "results_sha256": None,
    }
    payload["results_sha256"] = hashlib.sha256(canonical({key: value for key, value in payload.items() if key != "results_sha256"})).hexdigest()
    report_lines = [
        "# Crossasset V2 retrospective diagnostics — 2026-09-09",
        "",
        "Frozen before outcomes. All signals are retrospective non-PIT diagnostics; missing availability, valuation vintage, borrow, margin, settlement, revision, or executable fill evidence prevents admission.",
        "",
        f"Protocol SHA: `{protocol['protocol_sha256']}`  ",
        f"Results SHA: `{payload['results_sha256']}`  ",
        f"Overlay: `{payload['overlay_status']}`; economic run: `false`; admitted rows: `0`.",
        "",
        "| Card | Status | Signal rows | Full/recent/holdout | Admitted |",
        "|---|---|---:|---|---:|",
    ]
    for card in [f"X{i:02d}" for i in range(1, 7)]:
        row = cards[card]
        counts = row.get("windows", {})
        rendered = "/".join(str(counts.get(window, {}).get("rows", 0)) for window in ("full", "recent", "holdout"))
        report_lines.append(f"| {card} | {row['status']} | {row['signal_rows']} | {rendered} | {row['admitted_rows']} |")
    report_lines.extend(["", "Cost, turnover, drawdown/tail, correlation, bootstrap, permutation/placebo and two-leg execution are recorded as not run or blocked where their required outcome/leg inputs are absent. No OHLCV or current snapshot is used as a causal substitute.", ""])
    result_bytes = canonical(payload)
    result_file_sha = hashlib.sha256(result_bytes).hexdigest()
    report_bytes = "\n".join(report_lines).encode()
    manifest_payload = {
        "schema": "crossasset-retrospective-diagnostic-manifest-v2",
        "protocol": {"path": str(PROTOCOL.relative_to(REPO)), "sha256": sha256(PROTOCOL)},
        "inputs": [{"path": source["path"], "sha256": source["sha256"]} for source in protocol["source_inputs"]],
        "artifacts": {
            str(RESULTS.relative_to(REPO)): {"sha256": result_file_sha, "bytes": len(result_bytes)},
            str(REPORT.relative_to(REPO)): {"sha256": hashlib.sha256(report_bytes).hexdigest(), "bytes": len(report_bytes)},
        },
        "economic_run": False,
        "admitted_rows": 0,
        "manifest_sha256": "PENDING_FREEZE",
    }
    manifest_payload["manifest_sha256"] = hashlib.sha256(canonical({key: value for key, value in manifest_payload.items() if key != "manifest_sha256"})).hexdigest()
    manifest_bytes = canonical(manifest_payload)
    manifest_file_sha = hashlib.sha256(manifest_bytes).hexdigest()
    receipt_payload = {
        "schema": "crossasset-retrospective-diagnostic-receipt-v2",
        "protocol": {"path": str(PROTOCOL.relative_to(REPO)), "sha256": sha256(PROTOCOL)},
        "results": {"path": str(RESULTS.relative_to(REPO)), "sha256": result_file_sha},
        "report": {"path": str(REPORT.relative_to(REPO)), "sha256": hashlib.sha256(report_bytes).hexdigest()},
        "manifest": {"path": str(MANIFEST.relative_to(REPO)), "sha256": manifest_file_sha},
        "cards": 6,
        "diagnostic_cards": 3,
        "economic_run": False,
        "admitted_rows": 0,
        "economic_eligible": False,
        "promotion": False,
        "network_used": False,
        "receipt_sha256": "PENDING_FREEZE",
    }
    receipt_payload["receipt_sha256"] = hashlib.sha256(canonical({key: value for key, value in receipt_payload.items() if key != "receipt_sha256"})).hexdigest()
    atomic_write(RESULTS, result_bytes)
    atomic_write(REPORT, report_bytes)
    atomic_write(MANIFEST, manifest_bytes)
    atomic_write(RECEIPT, canonical(receipt_payload))
    return {"status": "CROSSASSET_MAX_TESTING_COMPLETE_V2", "cards": 6, "diagnostic_cards": 3, "admitted_rows": 0, "results_sha256": payload["results_sha256"], "manifest_sha256": manifest_payload["manifest_sha256"], "receipt_sha256": receipt_payload["receipt_sha256"]}


def verify(protocol_path: Path = PROTOCOL) -> dict[str, Any]:
    protocol = _protocol(protocol_path)
    _verified_sources(protocol)
    if not all(path.is_file() for path in (RESULTS, REPORT, MANIFEST, RECEIPT)):
        raise ValueError("diagnostic_artifact_missing")
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    expected_results = hashlib.sha256(canonical({key: value for key, value in payload.items() if key != "results_sha256"})).hexdigest()
    if payload.get("results_sha256") != expected_results or payload.get("protocol_sha256") != protocol["protocol_sha256"]:
        raise ValueError("results_hash_or_protocol_mismatch")
    if payload.get("counts") != {"cards": 6, "diagnostic_cards": 3, "admitted_rows": 0, "economic_run": 0, "economic_eligible": 0, "promotion": 0} or payload.get("overlay_status") != "NOT_RUN_NO_ELIGIBLE_CANDIDATE":
        raise ValueError("unsafe_results_flags")
    if set(payload.get("cards", {})) != {f"X{i:02d}" for i in range(1, 7)}:
        raise ValueError("card_set_mismatch")
    for card, row in payload["cards"].items():
        if row.get("admitted_rows") != 0 or row.get("economic_eligible") is not False or row.get("causal_admissible") is not False:
            raise ValueError(f"unsafe_card:{card}")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("manifest_sha256") != hashlib.sha256(canonical({key: value for key, value in manifest.items() if key != "manifest_sha256"})).hexdigest():
        raise ValueError("manifest_hash_mismatch")
    if manifest.get("artifacts", {}).get(str(RESULTS.relative_to(REPO)), {}).get("sha256") != sha256(RESULTS) or manifest.get("artifacts", {}).get(str(REPORT.relative_to(REPO)), {}).get("sha256") != sha256(REPORT):
        raise ValueError("manifest_artifact_mismatch")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("receipt_sha256") != hashlib.sha256(canonical({key: value for key, value in receipt.items() if key != "receipt_sha256"})).hexdigest():
        raise ValueError("receipt_hash_mismatch:self")
    expected = {"protocol": (PROTOCOL, sha256(PROTOCOL)), "results": (RESULTS, sha256(RESULTS)), "report": (REPORT, sha256(REPORT)), "manifest": (MANIFEST, sha256(MANIFEST))}
    for key, (path, digest) in expected.items():
        if receipt.get(key, {}).get("sha256") != digest:
            raise ValueError(f"receipt_hash_mismatch:{key}")
    return {"status": "CROSSASSET_RETROSPECTIVE_DIAGNOSTICS_VERIFIED", "cards": 6, "diagnostic_cards": 3, "admitted_rows": 0, "overlay_status": payload["overlay_status"], "read_only": True, "network_free": True, "results_sha256": payload["results_sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify() if args.verify else build(), sort_keys=True))


if __name__ == "__main__":
    main()
