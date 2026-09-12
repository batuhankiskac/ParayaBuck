#!/usr/bin/env python3
"""Independent verifier for the R46 v2 native-ledger contract.

This module intentionally does not import the runner.  It recomputes hashes,
cash, ownership, caps, dividends, and order boundaries from serialized data so
that a runner bug or a post-run edit cannot make a receipt verify itself.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from parayabuck.backtest.regulatory_fees import rates_for_trade_date
from scripts.research.r46_v3_oracle_2026_09_11 import (
    FROZEN_ACCOUNT_CAPS,
    CachedV3TargetOracle,
    PendingOrderLedger,
    add_overlay_weight,
    apply_account_risk,
)

RESEARCH_ID = "r46_treasury_auction_unwind_tlt_v2_2026_09_10"
REPO = Path(__file__).resolve().parents[2]
CANONICAL_MANIFEST_PATH = (
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10.json"
)
CANONICAL_PACKAGE_PATH = (
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_data_package.json"
)
CANONICAL_SCHEMA_PATH = (
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10.schema.json"
)
CANONICAL_STATISTICS_PATH = (
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_statistics_plan.json"
)
CANONICAL_FAMILY_PATH = (
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_family_registry.json"
)
CANONICAL_ONE_SHOT_PATH = (
    "data/research_manifests/r46_treasury_auction_unwind_tlt_v2_2026_09_10_one_shot_state.json"
)
CANONICAL_NATIVE_ATTESTATION_PATH = (
    "data/research_receipts/r46_treasury_auction_unwind_tlt_v2_2026_09_10_native_attestation.json"
)
CANONICAL_REVIEW_PATH = "data/research_receipts/r46_v2_independent_software_review_2026_09_10.json"
NATIVE_LEDGER_SCHEMA = "r46-v2-native-ledger-v2"
NATIVE_SMOKE_STATUS = "NATIVE_ADAPTER_SMOKE"
SYNTHETIC_STATUS = "TEST_ONLY_SYNTHETIC_LEDGER"
ECONOMIC_RUN_SCHEMA = "r46-v2-economic-run-v1"
ECONOMIC_LEDGER_STATUS = "R46_V2_ECONOMIC_RUN"
ATTESTATION_SCHEMA = "r46-native-ledger-attestation-v2"
FROZEN_CAPS: dict[str, float | int] = {
    "equity_gross_max": 0.55,
    "combined_gross_max": 0.60,
    "combined_cash_min": 0.40,
    "total_positions_max": 6,
}
_EPS = 1e-7
_HEX64 = set("0123456789abcdef")
_HEX40 = set("0123456789abcdef")
STATISTIC_IDS = (
    "R46_PRIMARY_EVENT_EPISODE_EXCESS_VS_EXPOSURE_MATCHED_NULL",
    "R46_INCUMBENT_COMPARISON",
    "R46_TIMING_PLACEBO",
    "R46_COST_2X",
    "R46_COST_3X",
    "R46_CAPACITY_SUPPRESSION",
    "R46_MISSINGNESS",
)
INFERENTIAL_STATISTIC_IDS = (
    STATISTIC_IDS[0],
    STATISTIC_IDS[1],
    STATISTIC_IDS[3],
    STATISTIC_IDS[4],
)
EXPECTED_COST_MULTIPLIERS = (1.0, 2.0, 3.0)

REQUIRED_DECISION_FIELDS = {
    "decision_timestamp",
    "signal_asof_timestamp",
    "target_weights",
    "suppression_reason",
    "ownership_attribution",
}
REQUIRED_FILL_FIELDS = {
    "timestamp",
    "symbol",
    "side",
    "qty",
    "price",
    "fee",
    "owner",
    "decision_timestamp",
    "client_order_id",
    "reference_price",
    "slippage_cost",
    "spread_cost",
}


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _bar_source_row_hash(bar: Any) -> str:
    return hashlib.sha256(
        _canonical_bytes(
            {
                "timestamp": bar.timestamp.isoformat(),
                "symbol": str(bar.symbol),
                "timeframe": str(bar.timeframe),
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
                "market": str(bar.market),
            }
        )
    ).hexdigest()


def _published_payload_hash(value: Mapping[str, Any]) -> str:
    encoded = (json.dumps(dict(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_hash(payload: Mapping[str, Any], field: str) -> str:
    value = json.loads(json.dumps(dict(payload), sort_keys=True, ensure_ascii=False))
    value.pop(field, None)
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _nested_canonical_hash(payload: Mapping[str, Any], field: str) -> str:
    value = json.loads(json.dumps(dict(payload), sort_keys=True, ensure_ascii=False))
    integrity = value.get("integrity")
    if not isinstance(integrity, dict):
        raise ValueError("integrity_object_required")
    integrity.pop(field, None)
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _number(value: object, field: str) -> float:
    try:
        number = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_not_numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field}_not_finite")
    return number


def _timestamp(value: object, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field}_must_be_timezone_aware")
    return parsed


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field}_invalid")
    return value


def _sequence(value: object, field: str) -> list[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field}_invalid")
    return list(value)


def _valid_hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


def _valid_hex40(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and set(value) <= _HEX40


def _repo_file(root: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"attestation_{field}_path_missing")
    candidate = Path(value)
    path = candidate if candidate.is_absolute() else root / candidate
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"attestation_{field}_path_outside_repo") from exc
    if not resolved.is_file():
        raise ValueError(f"attestation_{field}_path_missing")
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_artifact_file_hash(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("attestation_artifact_invalid")
    integrity = payload.get("integrity")
    declared = integrity.get("manifest_sha256") if isinstance(integrity, Mapping) else None
    if not isinstance(declared, str) or not _valid_hex64(declared):
        raise ValueError("attestation_artifact_hash_missing")
    computed = _nested_canonical_hash(payload, "manifest_sha256")
    if declared != computed:
        raise ValueError("attestation_artifact_hash_invalid")
    return computed


def _verify_file_pins(payload: Mapping[str, Any], *, repo_root: Path) -> None:
    artifact_specs = (
        ("governance_manifest_path", "governance_canonical_sha256", "manifest_sha256"),
        ("data_package_manifest_path", "package_canonical_sha256", "manifest_sha256"),
    )
    for path_field, hash_field, _ in artifact_specs:
        path = _repo_file(repo_root, payload.get(path_field), path_field)
        actual = _canonical_artifact_file_hash(path)
        if payload.get(hash_field) != actual:
            raise ValueError(f"ATTESTATION_PIN_MISMATCH:{hash_field}")
    source_specs = (
        ("runner_path", "runner_sha256"),
        ("verifier_path", "verifier_sha256"),
        ("source_path", "source_sha256"),
    )
    for path_field, hash_field in source_specs:
        path = _repo_file(repo_root, payload.get(path_field), path_field)
        actual = _sha256_file(path)
        if payload.get(hash_field) != actual:
            raise ValueError(f"ATTESTATION_PIN_MISMATCH:{hash_field}")
    source_hashes = payload.get("source_hashes")
    if not isinstance(source_hashes, Mapping):
        raise ValueError("ATTESTATION_SOURCE_PINS_MISSING")
    for relative, declared in source_hashes.items():
        path = _repo_file(repo_root, relative, "source")
        if declared != _sha256_file(path):
            raise ValueError(f"ATTESTATION_PIN_MISMATCH:source:{relative}")


def _verify_boundary(payload: Mapping[str, Any], *, economic: bool = False) -> None:
    expected: dict[str, object] = {
        "research_only": True,
        "economic_execution": True if economic else False,
        "economic_run": True if economic else False,
        "outcome_artifacts_present": True if economic else False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "promotion": False,
        "integration_candidate": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
        "native_one_ledger": True,
        "shared_account": True,
        "separate_sleeve_blend": False,
    }
    if any(payload.get(field) is not value for field, value in expected.items()):
        raise ValueError("research_only_boundary")
    if payload.get("research_id") != RESEARCH_ID or payload.get("candidate_id") != "R46":
        raise ValueError("ledger_identity")


def _verify_caps(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    caps = _mapping(payload.get("caps"), "caps")
    for key, expected in FROZEN_CAPS.items():
        actual = caps.get(key)
        if key == "total_positions_max":
            if actual != expected:
                raise ValueError("frozen_caps_drift")
        elif abs(_number(actual, key) - float(expected)) > _EPS:
            raise ValueError("frozen_caps_drift")
    return caps


def _verify_fill_shape(
    record: Mapping[str, Any],
    *,
    require_filled: bool = False,
) -> tuple[str, str, float, float, float, str, datetime, float, float, float]:
    if not REQUIRED_FILL_FIELDS.issubset(record):
        raise ValueError("fill_fields_missing")
    timestamp = _timestamp(record["timestamp"], "fill_timestamp")
    decision_timestamp = _timestamp(record["decision_timestamp"], "decision_timestamp")
    symbol = str(record.get("symbol", "")).strip()
    side = str(record.get("side", "")).upper().strip()
    owner = str(record.get("owner", "")).strip()
    qty = _number(record.get("qty"), "fill_qty")
    price = _number(record.get("price"), "fill_price")
    fee = _number(record.get("fee"), "fill_fee")
    reference_price = _number(record.get("reference_price"), "reference_price")
    slippage_cost = _number(record.get("slippage_cost"), "slippage_cost")
    spread_cost = _number(record.get("spread_cost"), "spread_cost")
    client_order_id = record.get("client_order_id")
    if (
        not symbol
        or side not in {"BUY", "SELL"}
        or not owner
        or qty <= 0
        or price <= 0
        or fee < 0
        or reference_price <= 0
        or slippage_cost < 0
        or spread_cost < 0
        or not isinstance(client_order_id, str)
        or not client_order_id.strip()
    ):
        raise ValueError("fill_invalid")
    if require_filled:
        if record.get("status") != "FILLED":
            raise ValueError("fill_status_invalid")
        notional = _number(record.get("notional"), "fill_notional")
        if abs(notional - qty * price) > _EPS:
            raise ValueError("fill_notional_reconciliation")
        if decision_timestamp > timestamp:
            raise ValueError("fill_decision_after_execution")
    return symbol, side, qty, price, fee, owner, timestamp, reference_price, slippage_cost, spread_cost


def _verify_cost_contract(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    contract = _mapping(payload.get("cost_contract"), "cost_contract")
    required = {
        "slippage_bps",
        "spread_bps",
        "commission",
        "crypto_taker_fee_bps",
        "equity_taf_per_share",
        "equity_taf_cap",
        "equity_cat_per_share",
        "equity_sec_fee_rate",
        "equity_use_effective_dated_fees",
        "equity_fee_multiplier",
    }
    if not required.issubset(contract):
        raise ValueError("cost_contract_fields_missing")
    for field in required - {"equity_use_effective_dated_fees"}:
        if _number(contract.get(field), field) < 0:
            raise ValueError("cost_contract_invalid")
    if not isinstance(contract.get("equity_use_effective_dated_fees"), bool):
        raise ValueError("cost_contract_invalid")
    return contract


def _expected_fill_costs(
    *,
    symbol: str,
    side: str,
    qty: float,
    price: float,
    reference_price: float,
    timestamp: datetime,
    contract: Mapping[str, Any],
) -> tuple[float, float, float, float]:
    slippage_bps = _number(contract["slippage_bps"], "slippage_bps")
    spread_bps = _number(contract["spread_bps"], "spread_bps")
    commission = _number(contract["commission"], "commission")
    adverse = slippage_bps + spread_bps / 2.0
    direction = 1.0 if side == "BUY" else -1.0
    expected_price = reference_price * (1.0 + direction * adverse / 10_000.0)
    expected_slippage = qty * reference_price * slippage_bps / 10_000.0
    expected_spread = qty * reference_price * (spread_bps / 2.0) / 10_000.0
    is_crypto = "/" in symbol
    if is_crypto:
        expected_fee = commission + qty * price * _number(contract["crypto_taker_fee_bps"], "crypto_taker_fee_bps") / 10_000.0
    else:
        if bool(contract["equity_use_effective_dated_fees"]):
            rates = rates_for_trade_date(timestamp.date())
            taf_per_share = rates.taf_per_share
            taf_cap = rates.taf_cap
            cat_per_share = rates.cat_per_share
            sec_fee_rate = rates.sec_fee_rate
        else:
            taf_per_share = _number(contract["equity_taf_per_share"], "equity_taf_per_share")
            taf_cap = _number(contract["equity_taf_cap"], "equity_taf_cap")
            cat_per_share = _number(contract["equity_cat_per_share"], "equity_cat_per_share")
            sec_fee_rate = _number(contract["equity_sec_fee_rate"], "equity_sec_fee_rate")
        cat = qty * cat_per_share
        regulatory = cat
        if side == "SELL":
            taf = qty * taf_per_share
            if taf_cap > 0:
                taf = min(taf, taf_cap)
            regulatory += taf + qty * price * sec_fee_rate
        expected_fee = commission + regulatory * _number(contract["equity_fee_multiplier"], "equity_fee_multiplier")
    return expected_price, expected_slippage, expected_spread, expected_fee


def _verify_fill_cost(
    row: Mapping[str, Any], contract: Mapping[str, Any]
) -> tuple[str, str, float, float, float, str, datetime, float, float, float]:
    parsed = _verify_fill_shape(row, require_filled=True)
    symbol, side, qty, price, fee, owner, timestamp, reference_price, slippage_cost, spread_cost = parsed
    expected_price, expected_slippage, expected_spread, expected_fee = _expected_fill_costs(
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        reference_price=reference_price,
        timestamp=timestamp,
        contract=contract,
    )
    if (
        abs(price - expected_price) > _EPS
        or abs(slippage_cost - expected_slippage) > _EPS
        or abs(spread_cost - expected_spread) > _EPS
        or abs(fee - expected_fee) > _EPS
    ):
        raise ValueError("fill_cost_reconciliation")
    return parsed


def _verify_decisions(payload: Mapping[str, Any], caps: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    decisions = _sequence(payload.get("decisions"), "decisions")
    result: list[Mapping[str, Any]] = []
    for item in decisions:
        record = _mapping(item, "decision")
        if not REQUIRED_DECISION_FIELDS.issubset(record):
            raise ValueError("decision_fields_missing")
        decision_timestamp = _timestamp(record["decision_timestamp"], "decision_timestamp")
        signal_timestamp = _timestamp(record["signal_asof_timestamp"], "signal_asof_timestamp")
        if signal_timestamp > decision_timestamp:
            raise ValueError("signal_after_decision")
        weights = _sequence(record.get("target_weights"), "target_weights")
        symbols: set[str] = set()
        incumbent = 0.0
        combined = 0.0
        for item_weight in weights:
            weight = _mapping(item_weight, "target_weight")
            symbol = str(weight.get("symbol", "")).strip()
            owner = str(weight.get("owner", "")).strip()
            value = _number(weight.get("weight"), "target_weight")
            if not symbol or not owner or symbol in symbols or value < -_EPS or value > 1 + _EPS:
                raise ValueError("target_weight_invalid")
            symbols.add(symbol)
            combined += value
            if owner.lower() not in {"r46", "candidate"} and weight.get("candidate") is not True:
                incumbent += value
        if incumbent > float(caps["equity_gross_max"]) + _EPS:
            raise ValueError("incumbent_equity_gross_cap")
        if combined > float(caps["combined_gross_max"]) + _EPS:
            raise ValueError("combined_gross_cap")
        if 1.0 - combined < float(caps["combined_cash_min"]) - _EPS:
            raise ValueError("combined_cash_min")
        result.append(record)
    return result


def _verify_initial_positions(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    positions: dict[str, dict[str, Any]] = {}
    for item in _sequence(payload.get("initial_positions", ()), "initial_positions"):
        row = _mapping(item, "initial_position")
        symbol = str(row.get("symbol", "")).strip()
        owner = str(row.get("owner", "")).strip()
        qty = _number(row.get("qty"), "initial_qty")
        price = _number(row.get("price"), "initial_price")
        if not symbol or not owner or qty <= 0 or price <= 0 or symbol in positions:
            raise ValueError("initial_position_invalid")
        positions[symbol] = {"qty": qty, "price": price, "owner": owner}
    if len(positions) > int(FROZEN_CAPS["total_positions_max"]):
        raise ValueError("total_positions_max")
    return positions


def _verify_rejections(payload: Mapping[str, Any]) -> None:
    for item in _sequence(payload.get("rejected_orders", ()), "rejected_orders"):
        row = _mapping(item, "rejected_order")
        _verify_fill_shape(row)
        if row.get("status") != "REJECTED" or not str(row.get("reason", "")).strip():
            raise ValueError("rejected_order_fields_missing")


def _verify_synthetic_fills(
    payload: Mapping[str, Any],
    positions: dict[str, dict[str, Any]],
) -> tuple[float, float, list[Mapping[str, Any]]]:
    fills = _sequence(payload.get("fills"), "fills")
    expected_cash = _number(payload.get("initial_cash"), "initial_cash")
    if expected_cash <= 0:
        raise ValueError("initial_cash_invalid")
    for initial in positions.values():
        expected_cash -= _number(initial["qty"], "initial_qty") * _number(initial["price"], "initial_price")
    total_fees = 0.0
    applied: list[Mapping[str, Any]] = []
    last_timestamp: str | None = None
    saw_buy = False
    for item in fills:
        fill_record: Mapping[str, Any] = _mapping(item, "fill")
        symbol, side, qty, price, fee, owner, timestamp, _reference_price, _slippage_cost, _spread_cost = _verify_fill_shape(
            fill_record
        )
        timestamp_key = timestamp.isoformat()
        if timestamp_key != last_timestamp:
            last_timestamp = timestamp_key
            saw_buy = False
        if side == "SELL":
            if saw_buy:
                raise ValueError("sell_after_buy")
            holding = positions.get(symbol)
            if holding is None or owner != str(holding["owner"]):
                raise ValueError("filled_sell_exceeds_or_mixes_position")
            if qty > float(holding["qty"]) + _EPS:
                raise ValueError("filled_sell_exceeds_or_mixes_position")
            holding["qty"] = float(holding["qty"]) - qty
            if holding["qty"] <= _EPS:
                positions.pop(symbol, None)
            expected_cash += qty * price - fee
        else:
            saw_buy = True
            holding = positions.get(symbol)
            if holding is not None and owner != str(holding["owner"]):
                raise ValueError("mixed_symbol_ownership")
            if holding is None:
                if len(positions) >= int(FROZEN_CAPS["total_positions_max"]):
                    raise ValueError("total_positions_max")
                positions[symbol] = {"qty": qty, "price": price, "owner": owner}
            else:
                old_qty = float(holding["qty"])
                old_price = float(holding["price"])
                holding["price"] = (old_qty * old_price + qty * price) / (old_qty + qty)
                holding["qty"] = old_qty + qty
            expected_cash -= qty * price + fee
        total_fees += fee
        applied.append(fill_record)
    if expected_cash < -_EPS:
        raise ValueError("cash_reconciliation")
    return expected_cash, total_fees, applied


def _verify_synthetic_snapshots(
    payload: Mapping[str, Any],
    positions: Mapping[str, Mapping[str, Any]],
    expected_cash: float,
    total_fees: float,
    caps: Mapping[str, Any],
) -> None:
    snapshots = _sequence(payload.get("snapshots"), "snapshots")
    if not snapshots:
        return
    previous_fees: float | None = None
    for item in snapshots:
        snapshot = _mapping(item, "snapshot")
        required = {"timestamp", "cash", "equity", "gross_exposure", "cash_weight", "positions", "realized_holdings", "fees"}
        if not required.issubset(snapshot):
            raise ValueError("snapshot_fields_missing")
        _timestamp(snapshot["timestamp"], "snapshot_timestamp")
        cash = _number(snapshot["cash"], "snapshot_cash")
        equity = _number(snapshot["equity"], "snapshot_equity")
        gross = _number(snapshot["gross_exposure"], "snapshot_gross")
        cash_weight = _number(snapshot["cash_weight"], "snapshot_cash_weight")
        fees = _number(snapshot["fees"], "snapshot_fees")
        if fees < -_EPS or (previous_fees is not None and fees + _EPS < previous_fees):
            raise ValueError("snapshot_fee_order")
        previous_fees = fees
        observed_positions = _mapping(snapshot["positions"], "snapshot_positions")
        realized = _mapping(snapshot["realized_holdings"], "snapshot_realized_holdings")
        if set(observed_positions) != set(realized):
            raise ValueError("snapshot_realized_holdings_reconciliation")
        if (
            cash < -_EPS
            or equity <= 0
            or gross < -_EPS
            or gross > float(caps["combined_gross_max"]) + _EPS
            or cash_weight < float(caps["combined_cash_min"]) - _EPS
            or cash_weight > 1.0 + _EPS
        ):
            raise ValueError("snapshot_cap_violation")
        if len(observed_positions) > int(caps["total_positions_max"]):
            raise ValueError("snapshot_position_cap")
        marked = cash
        invested = 0.0
        for symbol, raw in observed_positions.items():
            row = _mapping(raw, "snapshot_position")
            qty = _number(row.get("qty"), "snapshot_qty")
            mark = _number(row.get("mark"), "snapshot_mark")
            value = _number(row.get("value"), "snapshot_value")
            owner = str(row.get("owner", "")).strip()
            if qty <= 0 or mark <= 0 or abs(value - qty * mark) > _EPS or not owner:
                raise ValueError("snapshot_position_reconciliation")
            marked += value
            invested += value
            realized_row = _mapping(realized.get(symbol), "snapshot_realized_holding")
            if abs(_number(realized_row.get("qty"), "realized_qty") - qty) > _EPS or realized_row.get("owner") != owner:
                raise ValueError("snapshot_realized_holdings_reconciliation")
        expected_gross = invested / equity
        expected_cash_weight = cash / equity
        if abs(gross - expected_gross) > _EPS:
            raise ValueError("snapshot_gross_reconciliation")
        if abs(cash_weight - expected_cash_weight) > _EPS:
            raise ValueError("snapshot_cash_weight_reconciliation")
        if abs(marked - equity) > _EPS:
            raise ValueError("snapshot_reconciliation")
    last = _mapping(snapshots[-1], "snapshot")
    if abs(_number(last["fees"], "snapshot_fees") - total_fees) > _EPS:
        raise ValueError("snapshot_fee_total_reconciliation")
    if abs(_number(last["cash"], "snapshot_cash") - expected_cash) > _EPS:
        raise ValueError("cash_reconciliation")
    observed = _mapping(last["positions"], "snapshot_positions")
    if set(observed) != set(positions):
        raise ValueError("position_reconciliation")
    for symbol, expected in positions.items():
        row = _mapping(observed[symbol], "snapshot_position")
        if abs(_number(row.get("qty"), "snapshot_qty") - float(expected["qty"])) > _EPS:
            raise ValueError("position_reconciliation")
        if row.get("owner") != expected["owner"]:
            raise ValueError("ownership_reconciliation")


def _verify_dividends(payload: Mapping[str, Any], *, native: bool) -> float:
    events = _sequence(payload.get("dividend_events"), "dividend_events")
    total = 0.0
    event_ids: set[str] = set()
    previous_timestamp: datetime | None = None
    for item in events:
        event = _mapping(item, "dividend_event")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip() or event_id in event_ids:
            raise ValueError("dividend_identity_invalid")
        event_ids.add(event_id)
        event_timestamp = _timestamp(event.get("timestamp"), "dividend_timestamp")
        if previous_timestamp is not None and event_timestamp < previous_timestamp:
            raise ValueError("dividend_timestamp_order")
        previous_timestamp = event_timestamp
        symbol = str(event.get("symbol", "")).strip()
        qty = _number(event.get("qty"), "dividend_qty")
        per_share = _number(event.get("per_share"), "dividend_per_share")
        credited = _number(event.get("credited_cash"), "credited_cash")
        if not symbol or qty < 0 or per_share <= 0 or credited < 0 or abs(credited - qty * per_share) > _EPS:
            raise ValueError("dividend_reconciliation")
        if native and event.get("action_type") not in {"dividend", "DIVIDEND"}:
            raise ValueError("native_dividend_action_type")
        total += credited
    declared = _number(payload.get("dividend_total", 0.0), "dividend_total")
    if abs(total - declared) > _EPS:
        raise ValueError("dividend_reconciliation")
    return total


def _verify_inner_ledger_hash(payload: Mapping[str, Any]) -> None:
    """Require the exact inner hash before any structural field is trusted.

    ``_verify_economic_structural`` is intentionally private, but it is also
    called by the outer verifier.  Keeping the hash check here prevents a
    future caller from accidentally turning that path into a mutable-object
    bypass of the serialized ledger contract.
    """

    expected_hash = payload.get("ledger_sha256")
    if not isinstance(expected_hash, str) or expected_hash != _canonical_hash(payload, "ledger_sha256"):
        raise ValueError("LEDGER_HASH_MISMATCH")


def _verify_native_structural(payload: Mapping[str, Any]) -> dict[str, object]:
    if payload.get("status") != NATIVE_SMOKE_STATUS or payload.get("test_only_synthetic") is not False:
        raise ValueError("native_adapter_status")
    if payload.get("native_engine") not in {"PortfolioBacktestEngine", "TerminalAwareResearchEngine"} or payload.get(
        "engine_invocations"
    ) != 1:
        raise ValueError("native_engine_invocation")
    if _number(payload.get("validated_bar_count"), "validated_bar_count") <= 0:
        raise ValueError("validated_bars_missing")
    action_count = int(_number(payload.get("explicit_corporate_action_count"), "explicit_corporate_action_count"))
    if action_count <= 0:
        raise ValueError("explicit_dividend_action_missing")
    fills = _sequence(payload.get("fills"), "fills")
    cost_contract = _verify_cost_contract(payload)
    expected_cash = _number(payload.get("initial_cash"), "initial_cash")
    positions: dict[str, dict[str, Any]] = {}
    fees = 0.0
    slippage_total = 0.0
    spread_total = 0.0
    last_fill_timestamp: datetime | None = None
    fill_identities: set[tuple[str, str, str, float, float, str]] = set()
    fill_order_identity: dict[str, tuple[str, str, datetime, str]] = {}
    fill_order_last_timestamp: dict[str, datetime] = {}
    last_timestamp: str | None = None
    saw_buy = False
    for item in fills:
        row = _mapping(item, "fill")
        symbol, side, qty, price, fee, owner, timestamp, _reference_price, slippage_cost, spread_cost = _verify_fill_cost(
            row, cost_contract
        )
        client_order_id = row.get("client_order_id")
        if not isinstance(client_order_id, str) or not client_order_id:
            raise ValueError("fill_identity_duplicate")
        decision_timestamp = _timestamp(row.get("decision_timestamp"), "fill_decision_timestamp")
        order_identity = (symbol, side, decision_timestamp, owner)
        previous_identity = fill_order_identity.get(client_order_id)
        if previous_identity is not None and previous_identity != order_identity:
            raise ValueError("fill_identity_duplicate")
        previous_execution = fill_order_last_timestamp.get(client_order_id)
        if previous_execution is not None and timestamp <= previous_execution:
            raise ValueError("fill_timestamp_order")
        fill_order_identity[client_order_id] = order_identity
        fill_order_last_timestamp[client_order_id] = timestamp
        identity = (client_order_id, symbol, side, qty, price, timestamp.isoformat())
        if identity in fill_identities:
            raise ValueError("fill_identity_duplicate")
        fill_identities.add(identity)
        if last_fill_timestamp is not None and timestamp < last_fill_timestamp:
            raise ValueError("fill_timestamp_order")
        last_fill_timestamp = timestamp
        timestamp_key = timestamp.isoformat()
        if timestamp_key != last_timestamp:
            last_timestamp = timestamp_key
            saw_buy = False
        if side == "SELL":
            if saw_buy:
                raise ValueError("sell_after_buy")
            holding = positions.get(symbol)
            if holding is None or owner != holding["owner"] or qty > float(holding["qty"]) + _EPS:
                raise ValueError("native_owner_or_quantity_mismatch")
            holding["qty"] = float(holding["qty"]) - qty
            if holding["qty"] <= _EPS:
                positions.pop(symbol, None)
            expected_cash += qty * price - fee
        else:
            saw_buy = True
            holding = positions.get(symbol)
            if holding is not None and owner != holding["owner"]:
                raise ValueError("native_owner_or_quantity_mismatch")
            if holding is None:
                positions[symbol] = {"qty": qty, "price": price, "owner": owner}
            else:
                old_qty = float(holding["qty"])
                old_price = float(holding["price"])
                holding["price"] = (old_qty * old_price + qty * price) / (old_qty + qty)
                holding["qty"] = old_qty + qty
            expected_cash -= qty * price + fee
        fees += fee
        slippage_total += slippage_cost
        spread_total += spread_cost
    _verify_rejections(payload)
    dividend_events = _sequence(payload.get("dividend_events"), "dividend_events")
    if len(dividend_events) != action_count:
        raise ValueError("native_dividend_action_count")
    dividend_total = _verify_dividends(payload, native=True)
    for raw_event in dividend_events:
        event = _mapping(raw_event, "dividend_event")
        event_timestamp = _timestamp(event["timestamp"], "dividend_timestamp")
        event_symbol = str(event["symbol"])
        expected_qty = 0.0
        for raw_fill in fills:
            fill = _mapping(raw_fill, "fill")
            if str(fill["symbol"]) != event_symbol:
                continue
            fill_timestamp = _timestamp(fill["timestamp"], "fill_timestamp")
            if fill_timestamp < event_timestamp:
                expected_qty += _number(fill["qty"], "fill_qty") if str(fill["side"]).upper() == "BUY" else -_number(fill["qty"], "fill_qty")
        observed_qty = _number(event["qty"], "dividend_qty")
        if abs(max(0.0, expected_qty) - observed_qty) > _EPS:
            raise ValueError("dividend_holder_boundary")
    expected_cash += dividend_total
    terminal = _mapping(payload.get("terminal_liquidation"), "terminal_liquidation")
    cash_before = _number(terminal.get("cash_before"), "terminal_cash_before")
    cash_after = _number(terminal.get("cash_after"), "terminal_cash_after")
    cash_delta = _number(terminal.get("cash_delta"), "terminal_cash_delta")
    terminal_fee = _number(terminal.get("fee"), "terminal_fee")
    terminal_slippage = _number(terminal.get("slippage_cost"), "terminal_slippage_cost")
    terminal_spread = _number(terminal.get("spread_cost"), "terminal_spread_cost")
    if min(terminal_fee, terminal_slippage, terminal_spread) < -_EPS:
        raise ValueError("terminal_liquidation_cost_invalid")
    if abs(cash_before - expected_cash) > _EPS or abs(cash_after - (cash_before + cash_delta)) > _EPS:
        raise ValueError("terminal_cash_chain")
    if bool(terminal.get("applied")) is not bool(
        abs(cash_delta) > _EPS or terminal_fee > _EPS or terminal_slippage > _EPS or terminal_spread > _EPS
    ):
        raise ValueError("terminal_liquidation_status")
    expected_cash = cash_after
    fees += max(0.0, terminal_fee)
    slippage_total += max(0.0, terminal_slippage)
    spread_total += max(0.0, terminal_spread)
    liquidated_symbols = _sequence(terminal.get("liquidated_symbols", ()), "liquidated_symbols")
    if any(not isinstance(symbol, str) or not symbol.strip() for symbol in liquidated_symbols):
        raise ValueError("terminal_liquidation_symbols_invalid")
    if terminal.get("applied"):
        if not liquidated_symbols or any(str(symbol) not in positions for symbol in liquidated_symbols):
            raise ValueError("terminal_liquidation_positions_missing")
        for raw_symbol in liquidated_symbols:
            positions.pop(str(raw_symbol), None)
    elif liquidated_symbols:
        raise ValueError("terminal_liquidation_symbols_invalid")
    observed_cash = _number(payload.get("cash"), "cash")
    if abs(expected_cash - observed_cash) > _EPS or abs(cash_after - observed_cash) > _EPS:
        raise ValueError("cash_reconciliation")
    if abs(fees - _number(payload.get("fees"), "fees")) > _EPS:
        raise ValueError("fee_reconciliation")
    if abs(slippage_total - _number(payload.get("slippage_cost_total"), "slippage_cost_total")) > _EPS:
        raise ValueError("slippage_reconciliation")
    if abs(spread_total - _number(payload.get("spread_cost_total"), "spread_cost_total")) > _EPS:
        raise ValueError("spread_reconciliation")
    observed_positions = _mapping(payload.get("positions"), "positions")
    if set(observed_positions) != set(positions):
        raise ValueError("position_reconciliation")
    for symbol, expected in positions.items():
        observed = _mapping(observed_positions[symbol], "position")
        if abs(_number(observed.get("qty"), "position_qty") - float(expected["qty"])) > _EPS:
            raise ValueError("position_reconciliation")
        if observed.get("owner") != expected["owner"]:
            raise ValueError("ownership_reconciliation")
    if payload.get("realized_holdings") != payload.get("positions"):
        raise ValueError("realized_holdings_reconciliation")
    snapshots = _sequence(payload.get("snapshots"), "snapshots")
    if not snapshots:
        raise ValueError("snapshots_missing")
    _verify_synthetic_snapshots(payload, positions, expected_cash, fees, _mapping(payload.get("caps"), "caps"))
    return {
        "status": "VERIFIED",
        "schema_version": payload["schema_version"],
        "fill_count": len(fills),
        "dividend_credit_total": dividend_total,
        "native_engine": payload["native_engine"],
    }


def _verify_economic_structural(payload: Mapping[str, Any]) -> dict[str, object]:
    """Verify one candidate/control ledger independently of result metrics."""

    _verify_inner_ledger_hash(payload)
    if payload.get("status") != ECONOMIC_LEDGER_STATUS or payload.get("test_only_synthetic") is not False:
        raise ValueError("economic_ledger_status")
    if payload.get("native_engine") != "TerminalAwareResearchEngine" or payload.get("engine_invocations") != 1:
        raise ValueError("native_engine_invocation")
    if payload.get("arm") not in {"candidate", "control"} or payload.get("control") is not (payload.get("arm") == "control"):
        raise ValueError("economic_arm_invalid")
    if "rejected_orders" in payload or "unfilled_exits" in payload:
        raise ValueError("economic_residual_arrays_forbidden")
    forbidden_rebalance_fields = {
        "target_weights",
        "reason",
        "universe_version",
        "universe_snapshot_id",
        "risk_overlay_halted",
        "risk_overlay_reasons",
    }
    for raw_rebalance in _sequence(payload.get("rebalances"), "economic_rebalances"):
        if forbidden_rebalance_fields & set(_mapping(raw_rebalance, "economic_rebalance")):
            raise ValueError("economic_rebalance_metadata_forbidden")
    for raw_decision in _sequence(payload.get("decisions"), "economic_decisions"):
        if "rebalance_reason" in _mapping(raw_decision, "economic_decision"):
            raise ValueError("economic_rebalance_metadata_forbidden")
    multiplier = _number(payload.get("cost_multiplier"), "cost_multiplier")
    if multiplier not in EXPECTED_COST_MULTIPLIERS:
        raise ValueError("economic_cost_multiplier_invalid")
    for field in ("manifest_canonical_sha256", "package_canonical_sha256", "preflight_receipt_sha256", "dataset_digest"):
        if not _valid_hex64(payload.get(field)):
            raise ValueError("economic_source_pin_invalid")
    _verify_boundary(payload, economic=True)
    caps = _verify_caps(payload)
    _verify_decisions(payload, caps)
    # Reuse the native fill/cash/dividend recomputation, but do not let the
    # economic status bypass the strict structural checks.
    native_view = dict(payload)
    native_view["status"] = NATIVE_SMOKE_STATUS
    native_verified = _verify_native_structural(native_view)
    schedules = _sequence(payload.get("event_schedules"), "event_schedules")
    for raw in schedules:
        schedule = _mapping(raw, "event_schedule")
        required = {"event_id", "entry_open", "exit_open", "entry_decision", "exit_decision", "holding_intervals"}
        if not required.issubset(schedule):
            raise ValueError("event_schedule_fields_missing")
        entry = _timestamp(schedule["entry_open"], "entry_open")
        exit_open = _timestamp(schedule["exit_open"], "exit_open")
        entry_decision = _timestamp(schedule["entry_decision"], "entry_decision")
        exit_decision = _timestamp(schedule["exit_decision"], "exit_decision")
        if int(_number(schedule["holding_intervals"], "holding_intervals")) != 5:
            raise ValueError("holding_intervals_invalid")
        if not entry_decision < entry or not entry < exit_open or not exit_decision < exit_open:
            raise ValueError("event_open_boundary_invalid")
        future_opens = _sequence(schedule.get("future_open_timestamps"), "future_open_timestamps")
        if len(future_opens) < 6:
            raise ValueError("sixth_future_open_missing")
        parsed_future = [_timestamp(value, "future_regular_open") for value in future_opens[:6]]
        if parsed_future[0] != entry or parsed_future[5] != exit_open or parsed_future[4] != exit_decision:
            raise ValueError("sixth_future_open_boundary_invalid")
    records = _sequence(payload.get("event_records"), "event_records")
    event_ids = {str(_mapping(item, "event_record").get("event_id", "")) for item in records}
    schedule_ids = {str(_mapping(item, "event_schedule").get("event_id", "")) for item in schedules}
    if not event_ids.issubset(schedule_ids):
        raise ValueError("event_record_crosslink")
    exact_opens = {
        str(_mapping(schedule, "event_schedule")["entry_open"])
        for schedule in schedules
    } | {
        str(_mapping(schedule, "event_schedule")["exit_open"])
        for schedule in schedules
    }
    for raw_fill in _sequence(payload.get("fills"), "fills"):
        fill = _mapping(raw_fill, "fill")
        if fill.get("symbol") == "TLT" and str(fill.get("timestamp")) not in exact_opens:
            raise ValueError("tlt_fill_outside_exact_open")
    if payload.get("arm") == "candidate":
        raw_statuses = _sequence(payload.get("event_fill_statuses"), "event_fill_statuses")
        statuses = {
            str(_mapping(item, "event_fill_status").get("event_id")): _mapping(item, "event_fill_status")
            for item in raw_statuses
        }
        accepted_ids = {
            str(_mapping(item, "event_record").get("event_id"))
            for item in records
            if _mapping(item, "event_record").get("status") == "ACCEPTED"
        }
        if not accepted_ids.issubset(statuses):
            raise ValueError("event_fill_status_crosslink")
        for event_id in accepted_ids:
            status = statuses[event_id]
            entry_status = status.get("entry_status")
            exit_status = status.get("exit_status")
            eligible = status.get("eligible_episode")
            if entry_status not in {"FULL", "PARTIAL", "UNFILLED"} or exit_status not in {
                "FULL",
                "PARTIAL",
                "UNFILLED",
            } or not isinstance(eligible, bool):
                raise ValueError("event_fill_status_invalid")
            if eligible is not (entry_status == "FULL" and exit_status == "FULL"):
                raise ValueError("partial_event_marked_eligible")
    return {
        "status": "VERIFIED",
        "schema_version": payload["schema_version"],
        "fill_count": native_verified["fill_count"],
        "dividend_credit_total": native_verified["dividend_credit_total"],
        "cost_multiplier": multiplier,
        "arm": payload["arm"],
    }


def _verify_statistics(statistics: Mapping[str, Any], *, allow_blocked_placebo: bool = False) -> None:
    primary = _mapping(statistics.get("primary_estimand"), "primary_estimand")
    if (
        primary.get("id") != STATISTIC_IDS[0]
        or primary.get("estimand") != "initial_equity_portfolio_contribution"
        or primary.get("initial_equity_denominator") != 100_000.0
        or primary.get("aggregation") != "equal_weight_over_eligible_event_episodes"
        or primary.get("contrast") != "candidate_minus_exposure_matched_null"
    ):
        raise ValueError("primary_estimand_invalid")
    primary_rows = _sequence(primary.get("rows"), "primary_rows")
    null_ledger = _mapping(primary.get("null_ledger"), "null_ledger")
    if (
        null_ledger.get("schema") != "r46-exposure-matched-null-ledger-v1"
        or null_ledger.get("reservation_model") != "cash_reserved_same_notional_zero_rate"
        or null_ledger.get("cash_rate_annualized") != 0.0
        or null_ledger.get("initial_equity_denominator") != 100_000.0
        or null_ledger.get("same_reservation_decisions") is not True
    ):
        raise ValueError("null_ledger_contract_invalid")
    null_rows = _sequence(null_ledger.get("rows"), "null_ledger_rows")
    if len(null_rows) != len(primary_rows):
        raise ValueError("null_ledger_row_count_invalid")
    for primary_raw, null_raw in zip(primary_rows, null_rows, strict=True):
        primary_row = _mapping(primary_raw, "primary_row")
        null_row = _mapping(null_raw, "null_row")
        if (
            not isinstance(primary_row.get("event_id"), str)
            or null_row.get("event_id") != primary_row.get("event_id")
            or null_row.get("entry_decision") != primary_row.get("entry_decision")
            or primary_row.get("reservation_model") != "same_fill_notional_zero_rate"
            or primary_row.get("initial_equity_denominator") != 100_000.0
            or primary_row.get("null_cash_rate_annualized") != 0.0
            or _number(primary_row.get("reserved_notional"), "reserved_notional") <= 0
            or _number(null_row.get("reserved_notional"), "null_reserved_notional")
            != _number(primary_row.get("reserved_notional"), "reserved_notional")
            or _number(null_row.get("null_contribution"), "null_contribution") != 0.0
            or _number(null_row.get("cash_rate_annualized"), "null_cash_rate") != 0.0
        ):
            raise ValueError("null_ledger_reservation_invalid")
    placebo = _mapping(statistics.get("timing_placebo_plus_10_sessions"), "timing_placebo")
    if placebo.get("id") != STATISTIC_IDS[2] or placebo.get("shift_sessions") != 10 or placebo.get("seed") != 4603:
        raise ValueError("timing_placebo_invalid")
    placebo_status = placebo.get("status")
    if placebo_status == "REPORTED_NATIVE_ENGINE_DIAGNOSTIC_ONLY":
        if (
            placebo.get("native_engine") != "TerminalAwareResearchEngine"
            or placebo.get("engine_invocations") != 2
            or placebo.get("not_tested") is not False
            or not isinstance(placebo.get("candidate_metrics"), Mapping)
            or not isinstance(placebo.get("control_metrics"), Mapping)
            or not isinstance(placebo.get("candidate_minus_control"), Mapping)
            or not isinstance(placebo.get("event_episode_deltas"), Sequence)
            or not isinstance(placebo.get("event_episode_count"), int)
            or placebo.get("event_episode_count") != len(placebo.get("event_episode_deltas", ()))
            or not isinstance(placebo.get("p_value"), (int, float))
        ):
            raise ValueError("timing_placebo_engine_cell_invalid")
    elif not (
        allow_blocked_placebo
        and placebo_status in {"BLOCKED_TIMING_PLACEBO_NO_COMPLETE_EPISODES", "BLOCKED_TIMING_PLACEBO_ENGINE_CELL"}
        and placebo.get("not_tested") is False
        and placebo.get("p_value") is None
    ):
        raise ValueError("timing_placebo_engine_cell_missing")
    incumbent = _mapping(statistics.get("unchanged_incumbent_comparison"), "unchanged_incumbent_comparison")
    if (
        incumbent.get("status") != "REPORTED_NATIVE_ENGINE_DESCRIPTIVE_ONLY"
        or incumbent.get("native_engine") != "TerminalAwareResearchEngine"
        or incumbent.get("engine_invocations") != 2
        or not isinstance(incumbent.get("candidate_metrics"), Mapping)
        or not isinstance(incumbent.get("control_metrics"), Mapping)
        or incumbent.get("candidate_total_return") != incumbent["candidate_metrics"].get("total_return")
        or incumbent.get("incumbent_total_return") != incumbent["control_metrics"].get("total_return")
    ):
        raise ValueError("incumbent_comparison_invalid")
    incumbent_count = incumbent.get("event_episode_count")
    incumbent_rows = _sequence(incumbent.get("rows"), "incumbent_rows")
    if not isinstance(incumbent_count, int) or incumbent_count != len(incumbent_rows):
        raise ValueError("incumbent_event_episode_count_invalid")
    if incumbent_count:
        if incumbent.get("not_tested") is not False or not isinstance(incumbent.get("p_value"), (int, float)):
            raise ValueError("incumbent_comparison_not_tested")
        permutation_details = _mapping(incumbent.get("permutation"), "incumbent_permutation")
        if (
            permutation_details.get("method") != "paired_sign_flip_randomization_under_exchangeability"
            or permutation_details.get("repetitions") != 10000
            or permutation_details.get("seed") != 4602
            or not isinstance(permutation_details.get("p_value"), (int, float))
        ):
            raise ValueError("incumbent_permutation_invalid")
        for raw_row in incumbent_rows:
            row = _mapping(raw_row, "incumbent_row")
            required = {
                "event_id",
                "t0",
                "t1",
                "candidate_t0",
                "candidate_t1",
                "incumbent_t0",
                "incumbent_t1",
                "candidate_change",
                "incumbent_change",
                "delta",
            }
            if set(row) != required or not isinstance(row.get("event_id"), str):
                raise ValueError("incumbent_row_fields_invalid")
            candidate_change = (
                _number(row.get("candidate_t1"), "incumbent_candidate_t1")
                - _number(row.get("candidate_t0"), "incumbent_candidate_t0")
            ) / 100_000.0
            incumbent_change = (
                _number(row.get("incumbent_t1"), "incumbent_t1")
                - _number(row.get("incumbent_t0"), "incumbent_t0")
            ) / 100_000.0
            if (
                abs(_number(row.get("candidate_change"), "incumbent_candidate_change") - candidate_change) > _EPS
                or abs(_number(row.get("incumbent_change"), "incumbent_change") - incumbent_change) > _EPS
                or abs(_number(row.get("delta"), "incumbent_delta") - (candidate_change - incumbent_change)) > _EPS
            ):
                raise ValueError("incumbent_row_formula_invalid")
    elif incumbent.get("p_value") is not None or incumbent.get("not_tested") is not True:
        raise ValueError("incumbent_empty_sample_invalid")
    for field in ("capacity", "missingness"):
        if field in statistics:
            diagnostic = _mapping(statistics.get(field), field)
            if diagnostic.get("p_value") is not None or diagnostic.get("not_tested") is not True:
                raise ValueError("descriptive_diagnostic_inferential_leak")
    resampling = _mapping(statistics.get("resampling"), "resampling")
    bootstrap = _mapping(resampling.get("bootstrap"), "bootstrap")
    permutation = _mapping(resampling.get("permutation"), "permutation")
    if bootstrap.get("repetitions") != 10000 or bootstrap.get("resamples") not in {0, 10000} or bootstrap.get("block_length") != 5 or bootstrap.get("seed") != 4601:
        raise ValueError("bootstrap_contract_invalid")
    if (
        permutation.get("method") != "paired_sign_flip_randomization_under_exchangeability"
        or permutation.get("repetitions") != 10000
        or permutation.get("resamples") not in {0, 10000}
        or permutation.get("seed") != 4602
    ):
        raise ValueError("permutation_contract_invalid")
    holm = _mapping(statistics.get("holm"), "holm")
    if holm.get("family_size") != len(INFERENTIAL_STATISTIC_IDS) or tuple(holm.get("ids", ())) != INFERENTIAL_STATISTIC_IDS:
        raise ValueError("holm_family_invalid")
    adjusted = _mapping(holm.get("adjusted_p_values"), "adjusted_p_values")
    if set(adjusted) != set(INFERENTIAL_STATISTIC_IDS):
        raise ValueError("holm_adjusted_p_values_invalid")
    if holm.get("status") == "NOT_TESTED_INCOMPLETE_INFERENTIAL_DATA":
        if any(value is not None for value in adjusted.values()):
            raise ValueError("holm_not_tested_values_invalid")
    elif any(not 0 <= _number(value, "adjusted_p_value") <= 1 for value in adjusted.values()):
        raise ValueError("holm_adjusted_p_values_invalid")
    coverage = _mapping(statistics.get("coverage_gates"), "coverage_gates")
    if coverage.get("minimum_event_episodes") != 24 or coverage.get("minimum_holding_intervals") != 120:
        raise ValueError("coverage_gate_contract_invalid")
    if coverage.get("maximum_missing_rate") != 0.0 or coverage.get("maximum_capacity_suppression_rate") != 0.25:
        raise ValueError("coverage_gate_contract_invalid")
    if coverage.get("fifth_open_exit_complete") is not True:
        raise ValueError("fifth_open_exit_contract_invalid")
    if coverage.get("sixth_future_open_complete") is not True:
        raise ValueError("sixth_future_open_contract_invalid")
    if statistics.get("no_thresholds_or_selection") is not True:
        raise ValueError("statistics_selection_boundary")


def verify_economic_run(
    results: Mapping[str, Any],
    ledger_bundle: Mapping[str, Any],
    report: Mapping[str, Any] | None = None,
    run_receipt: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Verify the outer result/ledger/report package and all three cost cells."""

    if results.get("schema_version") != ECONOMIC_RUN_SCHEMA or results.get("status") not in {"PASS_RESEARCH_ONLY", "FAILED_VALID_FROZEN_TEST"}:
        raise ValueError("economic_results_schema")
    if results.get("research_id") != RESEARCH_ID or results.get("candidate_id") != "R46":
        raise ValueError("economic_results_identity")
    if results.get("native_engine") != "TerminalAwareResearchEngine":
        raise ValueError("economic_results_native_engine")
    _verify_boundary(results, economic=True)
    if results.get("cost_multipliers") != list(EXPECTED_COST_MULTIPLIERS):
        raise ValueError("economic_cost_grid_invalid")
    if results.get("no_best_cell_selection") is not True or results.get("paper_live_untouched") is not True or results.get("paper_or_live_activation") is not False or results.get("promotion") is not False or results.get("no_auto_promotion") is not True:
        raise ValueError("economic_boundary_invalid")
    for field in ("manifest_canonical_sha256", "package_canonical_sha256", "preflight_receipt_sha256", "dataset_digest"):
        if not _valid_hex64(results.get(field)):
            raise ValueError("economic_results_pin_invalid")
    for field in ("source_commit", "freeze_commit"):
        if not _valid_hex40(results.get(field)):
            raise ValueError("economic_results_source_pin_invalid")
    statistics_pins = _mapping(results.get("statistics_pins"), "statistics_pins")
    for field in ("statistics_plan_sha256", "family_registry_sha256"):
        if not _valid_hex64(statistics_pins.get(field)):
            raise ValueError("economic_statistics_pin_invalid")
    if (
        statistics_pins.get("family_size") != len(STATISTIC_IDS)
        or statistics_pins.get("reported_output_count") != len(STATISTIC_IDS)
        or statistics_pins.get("inferential_family_size") != len(INFERENTIAL_STATISTIC_IDS)
        or tuple(statistics_pins.get("inferential_ids", ())) != INFERENTIAL_STATISTIC_IDS
        or tuple(statistics_pins.get("statistic_ids", ())) != STATISTIC_IDS
    ):
        raise ValueError("economic_statistics_family_invalid")
    if ledger_bundle.get("schema_version") != f"{ECONOMIC_RUN_SCHEMA}:ledger" or ledger_bundle.get("status") != ECONOMIC_LEDGER_STATUS:
        raise ValueError("economic_ledger_bundle_schema")
    if ledger_bundle.get("native_engine") != "TerminalAwareResearchEngine":
        raise ValueError("economic_ledger_native_engine")
    _verify_boundary(ledger_bundle, economic=True)
    if ledger_bundle.get("no_best_cell_selection") is not True:
        raise ValueError("economic_selection_boundary")
    if ledger_bundle.get("statistics_pins") != results.get("statistics_pins"):
        raise ValueError("economic_statistics_crosslink")
    result_cells = _sequence(results.get("cells"), "result_cells")
    ledger_cells = _sequence(ledger_bundle.get("cells"), "ledger_cells")
    if len(result_cells) != 3 or len(ledger_cells) != 3:
        raise ValueError("economic_cost_cell_count")
    seen: set[float] = set()
    for result_raw, ledger_raw in zip(result_cells, ledger_cells, strict=True):
        result_cell = _mapping(result_raw, "result_cell")
        ledger_cell = _mapping(ledger_raw, "ledger_cell")
        multiplier = _number(result_cell.get("cost_multiplier"), "cost_multiplier")
        ledger_multiplier = _number(ledger_cell.get("cost_multiplier"), "cost_multiplier")
        if (
            multiplier in seen
            or multiplier not in EXPECTED_COST_MULTIPLIERS
            or ledger_multiplier != multiplier
        ):
            raise ValueError("economic_cost_cell_invalid")
        seen.add(multiplier)
        candidate_ledger = _mapping(ledger_cell.get("candidate"), "candidate_ledger")
        control_ledger = _mapping(ledger_cell.get("control"), "control_ledger")
        if (
            _number(candidate_ledger.get("cost_multiplier"), "candidate_cost_multiplier") != multiplier
            or _number(control_ledger.get("cost_multiplier"), "control_cost_multiplier") != multiplier
        ):
            raise ValueError("economic_inner_multiplier_crosslink")
        candidate_metrics = _mapping(result_cell.get("candidate_metrics"), "candidate_metrics")
        control_metrics = _mapping(result_cell.get("control_metrics"), "control_metrics")
        candidate_result_metrics = _mapping(candidate_ledger.get("result_metrics"), "candidate_result_metrics")
        control_result_metrics = _mapping(control_ledger.get("result_metrics"), "control_result_metrics")
        if dict(candidate_metrics) != dict(candidate_result_metrics) or dict(control_metrics) != dict(control_result_metrics):
            raise ValueError("economic_result_metrics_crosslink")
        _verify_economic_structural(candidate_ledger)
        _verify_economic_structural(control_ledger)
    statistics = _mapping(results.get("statistics"), "statistics")
    allow_blocked_placebo = results.get("status") == "FAILED_VALID_FROZEN_TEST"
    _verify_statistics(statistics, allow_blocked_placebo=allow_blocked_placebo)
    if report is not None:
        _verify_boundary(report, economic=True)
        if report.get("schema_version") != f"{ECONOMIC_RUN_SCHEMA}:report" or report.get("status") not in {"PASS_RESEARCH_ONLY", "FAILED_VALID_FROZEN_TEST"}:
            raise ValueError("economic_report_schema")
        if report.get("primary_estimand") != statistics.get("primary_estimand") or report.get("holm") != statistics.get("holm"):
            raise ValueError("economic_report_crosslink")
        if report.get("statistics_pins") != results.get("statistics_pins"):
            raise ValueError("economic_report_statistics_crosslink")
        _verify_statistics({
            "primary_estimand": report.get("primary_estimand"),
            "unchanged_incumbent_comparison": report.get("unchanged_incumbent_comparison"),
            "timing_placebo_plus_10_sessions": report.get("timing_placebo_plus_10_sessions"),
            "resampling": report.get("resampling"),
            "holm": report.get("holm"),
            "coverage_gates": report.get("coverage_gates"),
            "no_thresholds_or_selection": report.get("no_thresholds_or_selection"),
        }, allow_blocked_placebo=allow_blocked_placebo)
    if run_receipt is not None:
        _verify_boundary(run_receipt, economic=True)
        if run_receipt.get("schema_version") != f"{ECONOMIC_RUN_SCHEMA}:receipt":
            raise ValueError("economic_run_receipt_schema")
        if run_receipt.get("status") != results.get("status"):
            raise ValueError("economic_run_receipt_status")
        one_shot = run_receipt.get("one_shot")
        if not isinstance(one_shot, Mapping) or one_shot.get("status") != "CONSUMED":
            raise ValueError("economic_run_not_consumed")
        receipt_hash = run_receipt.get("receipt_sha256")
        if not isinstance(receipt_hash, str) or receipt_hash != _canonical_hash(run_receipt, "receipt_sha256"):
            raise ValueError("economic_run_receipt_hash")
        for field in (
            "manifest_canonical_sha256",
            "package_canonical_sha256",
            "preflight_receipt_sha256",
            "runner_sha256",
            "verifier_sha256",
            "source_sha256",
        ):
            if not _valid_hex64(run_receipt.get(field)):
                raise ValueError("economic_run_receipt_pin_invalid")
        for field in ("source_commit", "freeze_commit"):
            if not _valid_hex40(run_receipt.get(field)):
                raise ValueError("economic_run_receipt_source_pin_invalid")
        if run_receipt.get("source_commit") != results.get("source_commit") or run_receipt.get("freeze_commit") != results.get("freeze_commit"):
            raise ValueError("economic_run_receipt_source_crosslink")
        if run_receipt.get("manifest_canonical_sha256") != results.get("manifest_canonical_sha256") or run_receipt.get("package_canonical_sha256") != results.get("package_canonical_sha256"):
            raise ValueError("economic_run_receipt_crosslink")
        if run_receipt.get("statistics_pins") != results.get("statistics_pins"):
            raise ValueError("economic_run_receipt_statistics_crosslink")
        outputs = _mapping(run_receipt.get("outputs"), "economic_outputs")
        expected_output_hashes = {
            "results_sha256": _published_payload_hash(results),
            "ledger_sha256": _published_payload_hash(ledger_bundle),
        }
        if report is not None:
            expected_output_hashes["report_sha256"] = _published_payload_hash(report)
        for field, expected in expected_output_hashes.items():
            if outputs.get(field) != expected:
                raise ValueError("economic_output_hash_crosslink")
        bundle_hash = hashlib.sha256(
            _canonical_bytes(
                {
                    "results_sha256": expected_output_hashes["results_sha256"],
                    "ledger_sha256": expected_output_hashes["ledger_sha256"],
                    "report_sha256": expected_output_hashes.get("report_sha256"),
                }
            )
        ).hexdigest()
        if outputs.get("bundle_sha256") != bundle_hash or one_shot.get("output_sha256") != bundle_hash:
            raise ValueError("economic_output_bundle_hash_crosslink")
    return {
        "status": "VERIFIED_ECONOMIC_RUN",
        "schema_version": results["schema_version"],
        "cost_cell_count": len(result_cells),
        "holm_family_size": len(INFERENTIAL_STATISTIC_IDS),
        "promotion": False,
    }


def _strict_json(path: Path, field: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"strict_{field}_unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError(f"strict_{field}_object_required")
    return value


def _strict_contained_file(root: Path, value: object, field: str, *, relative_only: bool = False) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"strict_{field}_path_missing")
    declared = Path(value)
    if relative_only and declared.is_absolute():
        raise ValueError(f"strict_{field}_path_must_be_relative")
    containment_root = root.resolve()
    candidate = declared if declared.is_absolute() else containment_root / declared
    try:
        relative_candidate = candidate.relative_to(containment_root)
    except ValueError:
        relative_candidate = None
    if relative_candidate is not None:
        current = containment_root
        for component in relative_candidate.parts:
            current /= component
            if current.is_symlink():
                raise ValueError(f"strict_{field}_symlink")
    elif candidate.is_symlink():
        raise ValueError(f"strict_{field}_symlink")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(containment_root)
    except ValueError as exc:
        raise ValueError(f"strict_{field}_outside_repo") from exc
    if not resolved.is_file():
        raise ValueError(f"strict_{field}_missing")
    return resolved


def _strict_relative(root: Path, path: Path, field: str) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError as exc:
        raise ValueError(f"strict_{field}_outside_repo") from exc


def _strict_hex(value: object, length: int, field: str) -> str:
    if not isinstance(value, str) or len(value) != length or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"strict_{field}_invalid")
    return value


def _strict_artifact(
    path: Path,
    field: str,
    *,
    schema: str | None = None,
    integrity_field: str = "manifest_sha256",
) -> dict[str, Any]:
    payload = _strict_json(path, field)
    if schema is not None and payload.get("schema_version", payload.get("schema")) != schema:
        raise ValueError(f"strict_{field}_schema")
    integrity = payload.get("integrity")
    declared = integrity.get(integrity_field) if isinstance(integrity, Mapping) else None
    if not isinstance(declared, str) or (
        declared
        != (
            _nested_canonical_hash(payload, integrity_field)
            if integrity_field != "manifest_sha256"
            else _canonical_artifact_file_hash(path)
        )
    ):
        raise ValueError(f"strict_{field}_canonical_hash")
    return payload


def _strict_git_object(root: Path, object_name: str, field: str) -> None:
    """Require a real commit object without trusting a 40-character shape."""

    import subprocess

    if not (root / ".git").exists():
        raise ValueError(f"strict_{field}_git_missing")
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{object_name}^{{commit}}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ValueError(f"strict_{field}_git_unavailable") from exc
    if completed.returncode != 0:
        raise ValueError(f"strict_{field}_commit_missing")


def _strict_git_lineage(root: Path, *, base: str, source: str, freeze: str) -> None:
    """Require C_base -> C_src -> C_freeze and that freeze is checked out."""

    import subprocess

    for value, field in ((base, "base_commit"), (source, "source_commit"), (freeze, "freeze_commit")):
        _strict_hex(value, 40, field)
        _strict_git_object(root, value, field)
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        if head.returncode != 0 or head.stdout.strip() != freeze:
            raise ValueError("strict_freeze_commit_not_head")
        for ancestor, descendant, field in (
            (base, source, "base_source_ancestry"),
            (source, freeze, "source_freeze_ancestry"),
        ):
            result = subprocess.run(
                ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise ValueError(f"strict_{field}")
    except OSError as exc:
        raise ValueError("strict_git_unavailable") from exc


def _strict_git_blob(root: Path, path: Path, declared: object, field: str, commits: Sequence[str]) -> None:
    import subprocess

    if not isinstance(declared, str) or len(declared) not in {40, 64}:
        raise ValueError(f"strict_{field}_blob_pin_missing")
    try:
        relative = str(path.resolve().relative_to(root.resolve()))
    except ValueError as exc:
        raise ValueError(f"strict_{field}_blob_path") from exc
    for commit in commits:
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), "cat-file", "blob", f"{commit}:{relative}"],
                check=False,
                capture_output=True,
            )
        except OSError as exc:
            raise ValueError(f"strict_{field}_blob_unavailable") from exc
        if completed.returncode != 0:
            raise ValueError(f"strict_{field}_blob_missing")
        content = completed.stdout
        if len(declared) == 64:
            actual = hashlib.sha256(content).hexdigest()
        else:
            header = f"blob {len(content)}\0".encode("ascii")
            actual = hashlib.sha1(header + content).hexdigest()
        if actual != declared:
            raise ValueError(f"strict_{field}_blob_mismatch")


def _strict_preflight_chain(
    *,
    root: Path,
    preflight_receipt_path: Path,
    expected_manifest_hash: object,
    expected_package_hash: object,
    expected_gate_hash: object,
) -> tuple[Path, Path, Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
    gate = _strict_artifact(
        preflight_receipt_path,
        "preflight",
        schema="r46-v2-preflight-receipt-v2",
        integrity_field="receipt_sha256",
    )
    if gate.get("status") != "PRE_RUN_OK" or gate.get("research_id") != RESEARCH_ID or gate.get("candidate_id") != "R46":
        raise ValueError("strict_preflight_identity")
    for field, expected in {
        "research_only": True,
        "read_only": True,
        "economic_execution": False,
        "economic_run": False,
        "economic_outcomes_accessed": False,
        "outcome_artifacts_present": False,
        "paper_live_untouched": True,
        "paper_or_live_activation": False,
        "integration_candidate": False,
        "promotion": False,
        "auto_promotion": False,
        "no_auto_promotion": True,
    }.items():
        if gate.get(field) is not expected:
            raise ValueError("strict_preflight_boundary")
    integrity = gate.get("integrity")
    if not isinstance(integrity, Mapping) or integrity.get("receipt_sha256") != _nested_canonical_hash(
        gate, "receipt_sha256"
    ):
        raise ValueError("strict_preflight_self_hash")
    if expected_gate_hash != _sha256_file(preflight_receipt_path) and expected_gate_hash != integrity.get("receipt_sha256"):
        raise ValueError("strict_preflight_raw_hash")
    manifest_ref = gate.get("manifest_path")
    package_ref = gate.get("package_path")
    manifest_path = _strict_contained_file(root, manifest_ref, "manifest", relative_only=True)
    package_path = _strict_contained_file(root, package_ref, "package", relative_only=True)
    if _strict_relative(root, manifest_path, "manifest") != CANONICAL_MANIFEST_PATH:
        raise ValueError("strict_canonical_manifest_path")
    if _strict_relative(root, package_path, "package") != CANONICAL_PACKAGE_PATH:
        raise ValueError("strict_canonical_package_path")
    manifest = _strict_artifact(manifest_path, "governance", schema="r46-governance-manifest-v2")
    package = _strict_artifact(package_path, "package", schema="r46-treasury-auction-unwind-data-package-v2")
    manifest_hash = manifest["integrity"]["manifest_sha256"]
    package_hash = package["integrity"]["manifest_sha256"]
    if expected_manifest_hash != manifest_hash or expected_package_hash != package_hash:
        raise ValueError("strict_manifest_package_crosslink")
    if gate.get("manifest_sha256") != manifest_hash or gate.get("package_sha256") != package_hash:
        raise ValueError("strict_preflight_source_crosslink")
    if gate.get("manifest_file_sha256") != _sha256_file(manifest_path) or gate.get("package_file_sha256") != _sha256_file(package_path):
        raise ValueError("strict_preflight_raw_source_hash")
    if manifest.get("research_id") != RESEARCH_ID or manifest.get("candidate_id") != "R46":
        raise ValueError("strict_governance_identity")
    if package.get("research_id") != RESEARCH_ID or package.get("candidate_id") != "R46":
        raise ValueError("strict_package_identity")
    data_contract = manifest.get("data_contract")
    if not isinstance(data_contract, Mapping) or data_contract.get("data_package_manifest_path") != _strict_relative(root, package_path, "package"):
        raise ValueError("strict_governance_package_path")
    evidence_contract = manifest.get("evidence_contract")
    if not isinstance(evidence_contract, Mapping) or evidence_contract.get("package_path") != _strict_relative(
        root, package_path, "package"
    ):
        raise ValueError("strict_evidence_package_path")
    if package.get("governance_manifest_path") != _strict_relative(root, manifest_path, "manifest"):
        raise ValueError("strict_package_governance_path")

    # The final gate must re-run the read-only v2 verifier over the exact
    # canonical package and its declared evidence.  A persisted PRE_RUN_OK
    # receipt is evidence, not authority: a caller cannot swap in a second
    # package, review, or one-shot file and re-hash the outer receipt.
    integrity = _mapping(manifest.get("integrity"), "strict_manifest_integrity")
    evaluation = _mapping(manifest.get("evaluation_contract"), "strict_evaluation_contract")
    statistics = _mapping(manifest.get("statistics"), "strict_statistics_contract")
    multiplicity = _mapping(manifest.get("multiplicity"), "strict_multiplicity_contract")
    evidence = _mapping(manifest.get("evidence_contract"), "strict_evidence_contract")
    data_contract = _mapping(manifest.get("data_contract"), "strict_data_contract")
    canonical_sidecars: dict[str, tuple[object, str]] = {
        "schema": (integrity.get("schema_path"), CANONICAL_SCHEMA_PATH),
        "statistics": (integrity.get("statistics_plan_path"), CANONICAL_STATISTICS_PATH),
        "family": (integrity.get("family_registry_path"), CANONICAL_FAMILY_PATH),
        "one_shot": (integrity.get("one_shot_state_path"), CANONICAL_ONE_SHOT_PATH),
        "native": (evidence.get("native_attestation_path"), CANONICAL_NATIVE_ATTESTATION_PATH),
        "review": (evidence.get("independent_software_review_path"), CANONICAL_REVIEW_PATH),
    }
    sidecars: dict[str, Path] = {}
    for field, (declared_path, expected_path) in canonical_sidecars.items():
        path = _strict_contained_file(root, declared_path, f"{field}_sidecar", relative_only=True)
        if _strict_relative(root, path, f"{field}_sidecar") != expected_path:
            raise ValueError(f"strict_canonical_{field}_path")
        sidecars[field] = path
    schema_path = sidecars["schema"]
    schema_hash = integrity.get("schema_sha256")
    if not isinstance(schema_hash, str) or schema_hash != _sha256_file(schema_path):
        raise ValueError("strict_schema_sidecar_hash")
    statistics_payload = _strict_artifact(
        sidecars["statistics"],
        "statistics_plan",
        schema="r46-statistics-plan-v2",
        integrity_field="artifact_sha256",
    )
    family_payload = _strict_artifact(
        sidecars["family"],
        "family_registry",
        schema="r46-multiplicity-family-v2",
        integrity_field="artifact_sha256",
    )
    one_shot_payload = _strict_artifact(
        sidecars["one_shot"],
        "one_shot_state",
        schema="r46-one-shot-state-v2",
        integrity_field="artifact_sha256",
    )
    if (
        statistics.get("plan_path") != CANONICAL_STATISTICS_PATH
        or statistics.get("plan_sha256") != statistics_payload.get("integrity", {}).get("artifact_sha256")
        or multiplicity.get("family_registry_path") != CANONICAL_FAMILY_PATH
        or multiplicity.get("family_registry_sha256") != family_payload.get("integrity", {}).get("artifact_sha256")
        or integrity.get("statistics_plan_path") != CANONICAL_STATISTICS_PATH
        or integrity.get("family_registry_path") != CANONICAL_FAMILY_PATH
    ):
        raise ValueError("strict_statistics_sidecar_crosslink")
    if (
        statistics_payload.get("reported_output_count") != len(STATISTIC_IDS)
        or _mapping(statistics_payload.get("inferential_family"), "strict_inferential_family").get("size")
        != len(INFERENTIAL_STATISTIC_IDS)
        or tuple(
            _mapping(statistics_payload.get("inferential_family"), "strict_inferential_family").get("ids", ())
        )
        != INFERENTIAL_STATISTIC_IDS
        or family_payload.get("family_size") != len(STATISTIC_IDS)
        or family_payload.get("reported_output_count") != len(STATISTIC_IDS)
        or family_payload.get("inferential_family_size") != len(INFERENTIAL_STATISTIC_IDS)
        or tuple(family_payload.get("inferential_ids", ())) != INFERENTIAL_STATISTIC_IDS
        or tuple(item.get("id") for item in family_payload.get("hypotheses", ()) if isinstance(item, Mapping))
        != STATISTIC_IDS
    ):
        raise ValueError("strict_statistics_family_contract")
    if (
        one_shot_payload.get("research_id") != RESEARCH_ID
        or one_shot_payload.get("candidate_id") != "R46"
        or one_shot_payload.get("status") != "SEALED_UNUSED"
    ):
        raise ValueError("strict_one_shot_sidecar_identity")
    if evaluation.get("one_shot_state_path") != CANONICAL_ONE_SHOT_PATH or integrity.get(
        "one_shot_state_path"
    ) != CANONICAL_ONE_SHOT_PATH:
        raise ValueError("strict_one_shot_sidecar_path")
    if evaluation.get("one_shot_state_sha256") != one_shot_payload.get("integrity", {}).get("artifact_sha256"):
        raise ValueError("strict_one_shot_sidecar_hash")

    # Require the package's exact five artifacts and the package acquisition
    # receipt as a regular file.  These paths are resolved from the package,
    # never from an attacker-controlled alternate manifest.
    required_artifacts = {"documents", "bars", "dividends", "eligibility", "calendar"}
    artifacts = package.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != required_artifacts:
        raise ValueError("strict_package_artifact_set")
    for name in sorted(required_artifacts):
        spec = _mapping(artifacts.get(name), f"strict_package_artifact:{name}")
        artifact_path = _strict_contained_file(root, spec.get("path"), f"package_artifact_{name}", relative_only=True)
        if not isinstance(spec.get("sha256"), str) or spec.get("sha256") != _sha256_file(artifact_path):
            raise ValueError(f"strict_package_artifact_hash:{name}")
    acquisition = _mapping(package.get("acquisition"), "strict_package_acquisition")
    pit_path = _strict_contained_file(root, acquisition.get("receipt_path"), "pit_source", relative_only=True)
    pit_relative = _strict_relative(root, pit_path, "pit_source")
    if not pit_relative or evidence.get("pit_source_attestation_path") not in (None, pit_relative):
        raise ValueError("strict_pit_source_path")
    source_matrix = data_contract.get("source_matrix_path")
    if not isinstance(source_matrix, str) or Path(source_matrix).is_absolute():
        raise ValueError("strict_source_matrix_path")
    source_matrix_path = _strict_contained_file(root, source_matrix, "source_matrix", relative_only=True)
    if data_contract.get("source_matrix_sha256") != _sha256_file(source_matrix_path):
        raise ValueError("strict_source_matrix_hash")

    # The independent review and native attestation are independently parsed
    # and checked; a matching raw hash alone is insufficient.
    review = _strict_json(sidecars["review"], "independent_review")
    source_provenance = _mapping(manifest.get("source_provenance"), "strict_source_provenance")
    if (
        review.get("schema_version") != "r46-independent-software-review-v1"
        or review.get("status") not in {"REVIEWED", "VALIDATED"}
        or review.get("research_id") != RESEARCH_ID
        or review.get("candidate_id") != "R46"
        or review.get("reviewed_commit") != source_provenance.get("source_commit")
        or review.get("governance_manifest_sha256") != manifest_hash
        or review.get("independence") != "independent_software_review"
        or not isinstance(review.get("reviewed_paths"), list)
        or not review.get("reviewed_paths")
        or review.get("integrity", {}).get("review_sha256") != _nested_canonical_hash(review, "review_sha256")
    ):
        raise ValueError("strict_independent_review_contract")
    try:
        from scripts.research.verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10 import verify_attestation

        native = _strict_json(sidecars["native"], "native_attestation")
        if (
            native.get("governance_canonical_sha256") != manifest_hash
            or native.get("package_canonical_sha256") != package_hash
            or native.get("governance_manifest_path") != CANONICAL_MANIFEST_PATH
            or native.get("data_package_manifest_path") != CANONICAL_PACKAGE_PATH
        ):
            raise ValueError("strict_native_attestation_crosslink")
        verify_attestation(native, repo_root=root)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("strict_native_attestation") from exc

    try:
        from scripts.research.r46_v2_preflight_2026_09_10 import verify_v2_manifest

        rechecked = verify_v2_manifest(
            manifest_path,
            data_package_manifest_path=package_path,
            pit_source_attestation_path=pit_path,
            native_ledger_attestation_path=sidecars["native"],
            independent_software_review_path=sidecars["review"],
            freeze_commit=str(gate.get("freeze_commit")),
            repo_root=root,
            preflight_receipt_path=preflight_receipt_path,
        )
    except (ImportError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("strict_preflight_revalidation") from exc
    if rechecked.get("status") != "PRE_RUN_OK" or rechecked.get("preflight_receipt") != gate:
        raise ValueError("strict_preflight_revalidation")
    return preflight_receipt_path, manifest_path, package_path, manifest, package, gate


def _strict_source_pins(
    *,
    root: Path,
    manifest: Mapping[str, Any],
    results: Mapping[str, Any],
    ledger: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> Mapping[str, Any]:
    source_paths = results.get("source_paths")
    ledger_source_paths = ledger.get("source_paths")
    # The inner ledgers are serialized evidence, not an optional convenience
    # field.  A missing top-level copy used to trigger the ``get(...,
    # source_paths)`` fallback and let the result's caller-controlled mapping
    # stand in for the ledger's exact source graph.
    if (
        not isinstance(source_paths, Mapping)
        or not isinstance(ledger_source_paths, Mapping)
        or dict(source_paths) != dict(ledger_source_paths)
    ):
        raise ValueError("strict_source_paths_crosslink")
    contract = manifest.get("data_contract")
    incumbent_contract = contract.get("incumbent_data_contract") if isinstance(contract, Mapping) else None
    if not isinstance(incumbent_contract, Mapping):
        raise ValueError("strict_v5_contract_missing")
    for key in ("bars", "lifetimes", "corporate_actions", "bars_manifest", "corporate_actions_manifest", "seam"):
        declared = incumbent_contract.get(key)
        actual = source_paths.get(key)
        if not isinstance(declared, Mapping) or not isinstance(actual, str) or Path(actual).is_absolute():
            raise ValueError(f"strict_source_path_invalid:{key}")
        if declared.get("path") != actual:
            raise ValueError(f"strict_source_path_crosslink:{key}")
        path = _strict_contained_file(root, actual, f"source_{key}", relative_only=True)
        raw_hash = source_paths.get(f"{key}_sha256")
        if raw_hash != declared.get("sha256") or raw_hash != _sha256_file(path):
            raise ValueError(f"strict_source_hash_mismatch:{key}")
    if source_paths.get("historical_cutoff") != "2026-07-31T23:59:59Z":
        raise ValueError("strict_source_cutoff")
    source_provenance = manifest.get("source_provenance")
    if not isinstance(source_provenance, Mapping):
        raise ValueError("strict_source_provenance_missing")
    source_commit = results.get("source_commit")
    freeze_commit = results.get("freeze_commit")
    _strict_hex(source_commit, 40, "source_commit")
    _strict_hex(freeze_commit, 40, "freeze_commit")
    if str(source_commit).lower().startswith("unset") or str(freeze_commit).lower().startswith("unset"):
        raise ValueError("strict_source_commit_placeholder")
    _strict_git_object(root, str(source_commit), "source_commit")
    _strict_git_object(root, str(freeze_commit), "freeze_commit")
    base_commit = source_provenance.get("base_commit")
    _strict_hex(base_commit, 40, "base_commit")
    if str(base_commit).lower().startswith("unset"):
        raise ValueError("strict_base_commit_placeholder")
    _strict_git_object(root, str(base_commit), "base_commit")
    _strict_git_lineage(root, base=str(base_commit), source=str(source_commit), freeze=str(freeze_commit))
    if source_provenance.get("clean_tree_required") is True:
        if source_provenance.get("clean_tree_observed") is not True:
            raise ValueError("strict_clean_tree_claim")
        import subprocess

        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            check=False,
            capture_output=True,
            text=True,
        )
        if status.returncode != 0 or status.stdout.strip():
            raise ValueError("strict_clean_tree")
    source_files = source_provenance.get("source_files")
    if not isinstance(source_files, Mapping) or not source_files:
        raise ValueError("strict_source_files_missing")
    critical_paths = {
        "runner": "scripts/research/r46_treasury_auction_unwind_tlt_v2_2026_09_10.py",
        "verifier": "scripts/research/verify_r46_treasury_auction_unwind_tlt_v2_2026_09_10.py",
        "v3_oracle": "scripts/research/r46_v3_oracle_2026_09_11.py",
        "preflight": "scripts/research/r46_v2_preflight_2026_09_10.py",
        "package": "scripts/research/build_r46_v2_data_package_2026_09_10.py",
        "builder": "scripts/research/build_r46_v2_governance_2026_09_10.py",
        "native_attestation": "scripts/research/build_r46_native_ledger_attestation_2026_09_10.py",
        "terminal_aware_engine": "src/parayabuck/backtest/terminal_aware_engine.py",
        "regulatory_fees": "src/parayabuck/backtest/regulatory_fees.py",
        "portfolio_engine": "src/parayabuck/backtest/portfolio_engine.py",
        "settings": "config/settings.yaml",
    }
    for role, expected_path in critical_paths.items():
        role_spec = source_provenance.get(role)
        if not isinstance(role_spec, Mapping) or role_spec.get("path") != expected_path:
            raise ValueError(f"strict_critical_role_path:{role}")
        if expected_path not in source_files:
            raise ValueError(f"strict_critical_source_missing:{expected_path}")
    for relative, spec in source_files.items():
        if not isinstance(relative, str) or not isinstance(spec, Mapping):
            raise ValueError("strict_source_file_pin_invalid")
        path = _strict_contained_file(root, relative, "source_file", relative_only=True)
        declared_sha = spec.get("sha256")
        if declared_sha is not None and declared_sha != _sha256_file(path):
            raise ValueError(f"strict_source_file_hash_mismatch:{relative}")
        blob_commits: tuple[str, ...] = (str(source_commit), str(freeze_commit))
        if spec.get("required_in_base_commit") is True:
            blob_commits = (str(base_commit), *blob_commits)
        _strict_git_blob(
            root,
            path,
            spec.get("git_blob_sha256"),
            f"source_file:{relative}",
            blob_commits,
        )
    for path_field, hash_field in (("runner_path", "runner_sha256"), ("verifier_path", "verifier_sha256"), ("source_path", "source_sha256")):
        role = path_field.removesuffix("_path")
        role_spec = source_provenance.get(role)
        if not isinstance(role_spec, Mapping) or receipt.get(path_field) != role_spec.get("path"):
            raise ValueError(f"strict_receipt_{path_field}_role_path")
        path = _strict_contained_file(root, receipt.get(path_field), path_field, relative_only=True)
        if receipt.get(hash_field) != _sha256_file(path):
            raise ValueError(f"strict_receipt_{hash_field}")
    for path_field, expected in (
        ("governance_manifest_path", manifest["integrity"]["manifest_sha256"]),
        ("data_package_manifest_path", _strict_artifact(_strict_contained_file(root, receipt.get("data_package_manifest_path"), "package", relative_only=True), "package")["integrity"]["manifest_sha256"]),
    ):
        if receipt.get(path_field) != _strict_relative(root, _strict_contained_file(root, receipt.get(path_field), path_field, relative_only=True), path_field):
            raise ValueError(f"strict_receipt_{path_field}")
        if path_field == "governance_manifest_path" and expected != manifest["integrity"]["manifest_sha256"]:
            raise ValueError("strict_receipt_governance_hash")
        if path_field == "data_package_manifest_path" and expected != results.get("package_canonical_sha256"):
            raise ValueError("strict_receipt_package_hash")
    return source_paths


def _strict_source_dataset(
    *,
    root: Path,
    package_path: Path,
    results: Mapping[str, Any],
    ledger_bundle: Mapping[str, Any],
) -> tuple[Any, Any, Any, dict[tuple[str, datetime], Any], tuple[Any, ...], tuple[datetime, ...]]:
    """Load the exact package/V5 source graph and index canonical executable bars."""

    try:
        from scripts.research import r46_treasury_auction_unwind_tlt_v2_2026_09_10 as runner
        from scripts.research.build_r46_v2_data_package_2026_09_10 import consume_r46_v2_data_package

        package_manifest = consume_r46_v2_data_package(package_path, repo_root=root)
        package_inputs = runner.load_r46_v2_package(package_path, repo_root=root)
        incumbent = runner.load_incumbent_pit_inputs(repo_root=root)
        dataset = runner.build_r46_market_dataset(package_inputs, incumbent)
    except Exception as exc:
        raise ValueError("strict_trusted_source_load") from exc
    digest = dataset.content_digest()
    if results.get("dataset_digest") != digest:
        raise ValueError("strict_dataset_digest")
    if ledger_bundle.get("dataset_digest") != digest:
        raise ValueError("strict_bundle_dataset_digest")
    bars: dict[tuple[str, datetime], Any] = {}
    for bar in dataset.executable_bars:
        key = (str(bar.symbol), bar.timestamp)
        if key in bars:
            raise ValueError("strict_duplicate_source_bar")
        bars[key] = bar
    signal_bars = tuple(
        sorted(
            (bar for bar in dataset.signal_bars if str(bar.symbol) == "SPY"),
            key=lambda bar: bar.timestamp,
        )
    )
    if not signal_bars:
        raise ValueError("strict_benchmark_source_missing")
    executable_timestamps = tuple(sorted({bar.timestamp for bar in dataset.executable_bars}))
    if not executable_timestamps:
        raise ValueError("strict_executable_source_missing")
    return package_manifest, package_inputs, incumbent, bars, signal_bars, executable_timestamps


def _strict_replay_inner(
    *,
    inner: Mapping[str, Any],
    contract: Mapping[str, Any],
    bars: Mapping[tuple[str, datetime], Any],
    dividend_rows_by_key: Mapping[str, tuple[datetime, float, datetime, str, str, str]],
    incumbent: Any,
    expected_curve_timestamps: Sequence[datetime] | None = None,
) -> dict[str, object]:
    """Replay an economic cell from trusted bars/actions, never ledger marks.

    Structural checks establish that serialized rows are well formed.  This
    second pass deliberately derives execution prices, regulatory fees, cash,
    quantities, weighted average cost, and terminal proceeds from the pinned
    source graph.  A re-hashed attacker payload therefore cannot choose its
    own reference price, dividend credit, or ending account state.
    """

    initial_cash = _number(inner.get("initial_cash"), "strict_initial_cash")
    if abs(initial_cash - 100_000.0) > _EPS:
        raise ValueError("strict_initial_cash_contract")
    cash = initial_cash
    fees = 0.0
    slippage_total = 0.0
    spread_total = 0.0
    dividend_total = 0.0
    closed_positions = 0
    holdings: dict[str, dict[str, Any]] = {}
    events: list[tuple[datetime, int, int, str, Mapping[str, Any]]] = []
    # Each history row is the post-event account state.  Fees are carried
    # alongside cash/holdings so snapshots can be checked against the trusted
    # cumulative fee sequence, including same-timestamp terminal fees.
    state_history: list[tuple[datetime, float, dict[str, dict[str, Any]], float]] = []
    # ``metric_history`` mirrors the engine's holdings audit.  In-cycle T1
    # removals are included after the terminal event; only end-only FINAL_BAR
    # liquidation is excluded because it overwrites curve[-1] after the final
    # holdings snapshot.
    metric_history: list[tuple[datetime, float, dict[str, dict[str, Any]], float]] = []
    ordered_source_bars: list[Any] = list(
        sorted(bars.values(), key=lambda bar: (bar.timestamp, str(bar.symbol)))
    )
    source_mark_index = 0
    latest_marks: dict[str, float] = {}

    def update_marks(timestamp: datetime) -> None:
        nonlocal source_mark_index
        while (
            source_mark_index < len(ordered_source_bars)
            and ordered_source_bars[source_mark_index].timestamp <= timestamp
        ):
            source_bar = ordered_source_bars[source_mark_index]
            if not (str(source_bar.symbol) == "TLT" and source_bar.timestamp in marker_timestamps):
                latest_marks[str(source_bar.symbol)] = float(source_bar.close)
            source_mark_index += 1

    def enforce_fill_caps() -> None:
        marked_value = sum(
            float(position["qty"]) * latest_marks.get(symbol, float(position["avg_price"]))
            for symbol, position in holdings.items()
        )
        equity = cash + marked_value
        if equity <= _EPS:
            raise ValueError("strict_replay_equity_nonpositive")
        combined_gross = marked_value / equity
        incumbent_gross = sum(
            float(position["qty"]) * latest_marks.get(symbol, float(position["avg_price"]))
            for symbol, position in holdings.items()
            if str(position.get("owner", "")).lower() not in {"r46", "candidate"}
        ) / equity
        cash_weight = cash / equity
        if (
            combined_gross > float(FROZEN_CAPS["combined_gross_max"]) + _EPS
            or incumbent_gross > float(FROZEN_CAPS["equity_gross_max"]) + _EPS
            or cash_weight < float(FROZEN_CAPS["combined_cash_min"]) - _EPS
            or cash_weight > 1.0 + _EPS
            or len(holdings) > int(FROZEN_CAPS["total_positions_max"])
        ):
            raise ValueError("strict_replay_fill_cap_violation")

    audit_context = _mapping(inner.get("audit_context"), "strict_replay_audit_context")
    if audit_context.get("execution_max_participation_pct") not in {0.10, 0.1}:
        raise ValueError("strict_replay_participation_contract")
    if audit_context.get("liquidity_lookback_bars") != 20:
        raise ValueError("strict_replay_liquidity_lookback_contract")
    marker_timestamps = {
        _timestamp(_mapping(raw, "strict_replay_event_schedule").get("entry_decision"), "strict_marker_timestamp")
        for raw in _sequence(inner.get("event_schedules", ()), "strict_replay_event_schedules")
    }
    aggregate_fill_qty: dict[tuple[str, datetime], float] = {}
    fill_order_identity: dict[str, tuple[str, str, datetime, str]] = {}
    fill_order_last_timestamp: dict[str, datetime] = {}
    fill_order_adv_cutoff: dict[str, datetime] = {}
    fills = _sequence(inner.get("fills"), "strict_replay_fills")
    previous_fill_timestamp: datetime | None = None
    for index, raw_fill in enumerate(fills):
        fill = _mapping(raw_fill, "strict_replay_fill")
        timestamp = _timestamp(fill.get("timestamp"), "strict_replay_fill_timestamp")
        if previous_fill_timestamp is not None and timestamp < previous_fill_timestamp:
            raise ValueError("strict_replay_fill_order")
        previous_fill_timestamp = timestamp
        client_order_id = fill.get("client_order_id")
        if not isinstance(client_order_id, str) or not client_order_id:
            raise ValueError("strict_replay_fill_identity")
        symbol = str(fill.get("symbol", ""))
        side = str(fill.get("side", "")).upper()
        qty = _number(fill.get("qty"), "strict_replay_fill_qty")
        price = _number(fill.get("price"), "strict_replay_fill_price")
        fee = _number(fill.get("fee"), "strict_replay_fill_fee")
        bar = bars.get((symbol, timestamp))
        if bar is None:
            raise ValueError("strict_replay_fill_source_bar")
        if fill.get("source_bar_key") != f"{symbol}|{timestamp.isoformat()}" or fill.get(
            "source_bar_row_sha256"
        ) != _bar_source_row_hash(bar):
            raise ValueError("strict_replay_fill_source_binding")
        decision_timestamp = _timestamp(fill.get("decision_timestamp"), "strict_replay_decision_timestamp")
        if decision_timestamp >= timestamp:
            raise ValueError("strict_replay_fill_causality")
        owner = str(fill.get("owner", ""))
        if not owner:
            raise ValueError("strict_replay_fill_owner")
        identity = (symbol, side, decision_timestamp, owner)
        previous_identity = fill_order_identity.get(client_order_id)
        if previous_identity is not None and previous_identity != identity:
            raise ValueError("strict_replay_fill_identity")
        previous_execution = fill_order_last_timestamp.get(client_order_id)
        if previous_execution is not None and timestamp <= previous_execution:
            raise ValueError("strict_replay_fill_order")
        adv_cutoff = fill_order_adv_cutoff.get(client_order_id, decision_timestamp)
        fill_order_identity[client_order_id] = identity
        fill_order_last_timestamp[client_order_id] = timestamp
        fill_order_adv_cutoff[client_order_id] = timestamp
        reference = float(bar.open)
        if abs(_number(fill.get("reference_price"), "strict_replay_reference") - reference) > _EPS:
            raise ValueError("strict_replay_reference_price")
        expected_price, expected_slippage, expected_spread, expected_fee = _expected_fill_costs(
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            reference_price=reference,
            timestamp=timestamp,
            contract=contract,
        )
        if (
            abs(price - expected_price) > _EPS
            or abs(fee - expected_fee) > _EPS
            or abs(_number(fill.get("slippage_cost"), "strict_replay_slippage") - expected_slippage) > _EPS
            or abs(_number(fill.get("spread_cost"), "strict_replay_spread") - expected_spread) > _EPS
            or abs(_number(fill.get("notional"), "strict_replay_notional") - qty * price) > _EPS
            or fill.get("status") != "FILLED"
        ):
            raise ValueError("strict_replay_fill_cost")
        if qty <= 0:
            raise ValueError("strict_replay_participation")
        aggregate_key = (symbol, timestamp)
        aggregate_fill_qty[aggregate_key] = aggregate_fill_qty.get(aggregate_key, 0.0) + qty
        if aggregate_fill_qty[aggregate_key] > float(bar.volume) * 0.10 + _EPS:
            raise ValueError("strict_replay_aggregate_participation")
        known_volumes = [
            float(source_bar.volume)
            for source_bar in ordered_source_bars
            if str(source_bar.symbol) == symbol
            and source_bar.timestamp <= adv_cutoff
            and not (symbol == "TLT" and source_bar.timestamp in marker_timestamps)
            and float(source_bar.volume) > 0
        ][-20:]
        if not known_volumes or aggregate_fill_qty[aggregate_key] > (sum(known_volumes) / len(known_volumes)) * 0.10 + _EPS:
            raise ValueError("strict_replay_liquidity_participation")
        events.append((timestamp, 1, index, "fill", fill))

    dividends = _sequence(inner.get("dividend_events"), "strict_replay_dividends")
    for index, raw_event in enumerate(dividends):
        event = _mapping(raw_event, "strict_replay_dividend")
        timestamp = _timestamp(event.get("timestamp"), "strict_replay_dividend_timestamp")
        events.append((timestamp, 0, index, "dividend", event))

    terminal = _mapping(inner.get("terminal_liquidation"), "strict_replay_terminal")
    terminal_rows = [
        _mapping(raw_row, "strict_replay_terminal_row")
        for raw_row in _sequence(inner.get("terminal_audit"), "strict_replay_terminal_audit")
    ]
    for index, raw_row in enumerate(terminal_rows):
        row = raw_row
        timestamp = _timestamp(row.get("last_bar"), "strict_replay_terminal_timestamp")
        events.append((timestamp, 2, index, "terminal", row))

    terminal_map = getattr(incumbent, "terminal_map", {}) or {}
    terminal_policy = str(audit_context.get("terminal_policy", "T1"))
    if terminal_policy != "T1":
        raise ValueError("strict_replay_terminal_policy_frozen")
    dividend_seen_keys: set[str] = set()
    terminal_seen_symbols: set[str] = set()
    # Keep the cash chain before terminal liquidation independent from the
    # chronological terminal rows.  T1 rows can occur in-cycle, while the
    # published terminal summary's cash_before is the account cash after all
    # ordinary fills/dividends and before any terminal proceeds.
    nonterminal_cash = cash
    for timestamp, _priority, _index, kind, row in sorted(events, key=lambda item: (item[0], item[1], item[2])):
        update_marks(timestamp)
        if kind == "dividend":
            source_key = row.get("source_event_key")
            source_row_hash = row.get("source_row_sha256")
            if not isinstance(source_key, str) or not isinstance(source_row_hash, str):
                raise ValueError("strict_replay_dividend_binding")
            source = dividend_rows_by_key.get(source_key)
            if source is None:
                raise ValueError("strict_replay_dividend_source")
            source_timestamp, source_amount, available_at, _document_hash, expected_row_hash, source_symbol = source
            symbol = str(row.get("symbol", ""))
            timestamp = _timestamp(row.get("timestamp"), "strict_replay_dividend_timestamp")
            expected_owner = "r46" if symbol == "TLT" else "incumbent_v3"
            if row.get("owner") != expected_owner:
                raise ValueError("strict_replay_dividend_owner")
            if (
                timestamp != source_timestamp
                or timestamp < available_at
                or source_row_hash != expected_row_hash
                or source_symbol and symbol != source_symbol
                or abs(_number(row.get("per_share"), "strict_replay_per_share") - source_amount) > _EPS
                or source_key in dividend_seen_keys
            ):
                raise ValueError("strict_replay_dividend_source")
            dividend_seen_keys.add(source_key)
            holding = holdings.get(symbol)
            expected_qty = float(holding["qty"]) if holding is not None else 0.0
            qty = _number(row.get("qty"), "strict_replay_dividend_qty")
            credited = _number(row.get("credited_cash"), "strict_replay_dividend_cash")
            if abs(qty - max(0.0, expected_qty)) > _EPS or abs(credited - qty * source_amount) > _EPS:
                raise ValueError("strict_replay_dividend_holder")
            expected_status = "CREDITED" if credited > _EPS else "NO_HOLDER"
            if row.get("status") != expected_status:
                raise ValueError("strict_replay_dividend_status")
            cash += credited
            nonterminal_cash += credited
            dividend_total += credited
            state_history.append(
                (timestamp, cash, {symbol: dict(value) for symbol, value in holdings.items()}, fees)
            )
            metric_history.append(
                (timestamp, cash, {symbol: dict(value) for symbol, value in holdings.items()}, fees)
            )
            continue

        if kind == "fill":
            symbol = str(row.get("symbol", ""))
            side = str(row.get("side", "")).upper()
            qty = _number(row.get("qty"), "strict_replay_fill_qty")
            price = _number(row.get("price"), "strict_replay_fill_price")
            fee = _number(row.get("fee"), "strict_replay_fill_fee")
            owner = str(row.get("owner", ""))
            if side == "BUY":
                existing = holdings.get(symbol)
                if existing is not None and existing["owner"] != owner:
                    raise ValueError("strict_replay_owner")
                if existing is None:
                    holdings[symbol] = {
                        "qty": qty,
                        "avg_price": price,
                        "owner": owner,
                        "entry_fee": fee,
                        "realized_pnl_accumulator": 0.0,
                    }
                else:
                    old_qty = float(existing["qty"])
                    existing["avg_price"] = (old_qty * float(existing["avg_price"]) + qty * price) / (old_qty + qty)
                    existing["qty"] = old_qty + qty
                    existing["entry_fee"] = float(existing.get("entry_fee", 0.0)) + fee
                cash -= qty * price + fee
                nonterminal_cash -= qty * price + fee
            elif side == "SELL":
                existing = holdings.get(symbol)
                if existing is None or existing["owner"] != owner or qty > float(existing["qty"]) + _EPS:
                    raise ValueError("strict_replay_sell_quantity")
                old_qty = float(existing["qty"])
                existing["qty"] = float(existing["qty"]) - qty
                if float(existing["qty"]) <= _EPS:
                    holdings.pop(symbol, None)
                    closed_positions += 1
                elif old_qty > _EPS:
                    existing["entry_fee"] = float(existing.get("entry_fee", 0.0)) * float(existing["qty"]) / old_qty
                cash += qty * price - fee
                nonterminal_cash += qty * price - fee
            else:
                raise ValueError("strict_replay_side")
            fees += fee
            slippage_total += _number(row.get("slippage_cost"), "strict_replay_slippage")
            spread_total += _number(row.get("spread_cost"), "strict_replay_spread")
            if cash < -_EPS:
                raise ValueError("strict_replay_cash_negative")
            enforce_fill_caps()
            state_history.append(
                (timestamp, cash, {symbol: dict(value) for symbol, value in holdings.items()}, fees)
            )
            metric_history.append(
                (timestamp, cash, {symbol: dict(value) for symbol, value in holdings.items()}, fees)
            )
            continue

        symbol = str(row.get("symbol", ""))
        if symbol in terminal_seen_symbols:
            raise ValueError("strict_replay_terminal_identity")
        terminal_seen_symbols.add(symbol)
        existing = holdings.get(symbol)
        if existing is None:
            raise ValueError("strict_replay_terminal_position")
        qty = _number(row.get("qty"), "strict_replay_terminal_qty")
        if abs(qty - float(existing["qty"])) > _EPS:
            raise ValueError("strict_replay_terminal_qty")
        bar = bars.get((symbol, timestamp))
        if bar is None or abs(_number(row.get("last_close"), "strict_replay_terminal_close") - float(bar.close)) > _EPS:
            raise ValueError("strict_replay_terminal_source")
        info = terminal_map.get(symbol)
        if info is not None:
            expected_policy = terminal_policy if bool(getattr(info, "should_haircut", True)) else "T1"
            if timestamp != getattr(info, "last_bar", timestamp):
                raise ValueError("strict_replay_terminal_map")
        else:
            expected_policy = "FINAL_BAR"
            symbol_bars = [bar_item.timestamp for (bar_symbol, _), bar_item in bars.items() if bar_symbol == symbol]
            if symbol_bars and timestamp != max(symbol_bars):
                raise ValueError("strict_replay_terminal_map")
        if row.get("effective_policy") != expected_policy or row.get("policy") not in {expected_policy, terminal_policy}:
            raise ValueError("strict_replay_terminal_policy")
        expected_owner = "r46" if symbol == "TLT" else "incumbent_v3"
        if row.get("owner") != expected_owner:
            raise ValueError("strict_replay_terminal_owner")
        entity_id = str(getattr(info, "entity_id", symbol)) if info is not None else symbol
        terminal_identity = f"{entity_id}|{timestamp.isoformat()}|{expected_policy}"
        expected_terminal_map_hash = hashlib.sha256(
            _canonical_bytes(
                {
                    "entity_id": entity_id,
                    "last_bar": timestamp.isoformat(),
                    "effective_policy": expected_policy,
                }
            )
        ).hexdigest()
        if (
            row.get("terminal_map_identity") != terminal_identity
            or row.get("terminal_map_hash") != expected_terminal_map_hash
        ):
            raise ValueError("strict_replay_terminal_map_identity")
        if abs(_number(row.get("avg_price"), "strict_replay_terminal_avg_price") - float(existing["avg_price"])) > _EPS:
            raise ValueError("strict_replay_terminal_basis")
        if existing.get("owner") != expected_owner:
            raise ValueError("strict_replay_terminal_owner")
        entry_fee = _number(row.get("entry_fee"), "strict_replay_terminal_entry_fee")
        if abs(entry_fee - float(existing.get("entry_fee", 0.0))) > _EPS:
            raise ValueError("strict_replay_terminal_basis")
        factor = {"T2": 0.5, "T3": 0.0}.get(expected_policy, 1.0)
        fill_price = _number(row.get("fill_price"), "strict_replay_terminal_price")
        expected_price, expected_slippage, expected_spread, expected_fee = _expected_fill_costs(
            symbol=symbol,
            side="SELL",
            qty=qty,
            price=fill_price,
            reference_price=float(bar.close) * factor,
            timestamp=timestamp,
            contract=contract,
        )
        if expected_policy == "T3":
            expected_price, expected_slippage, expected_spread, expected_fee = 0.0, 0.0, 0.0, 0.0
        proceeds = max(0.0, qty * expected_price - expected_fee)
        before_cash = cash
        # Terminal equity is marked with the latest trusted close as of the
        # terminal timestamp for every symbol, not only bars sharing the
        # terminal symbol's exact timestamp.
        mark_values = dict(latest_marks)
        pre_equity = before_cash + sum(
            float(position["qty"]) * mark_values.get(symbol_name, float(position["avg_price"]))
            for symbol_name, position in holdings.items()
        )
        expected_realized = (
            (expected_price - float(existing["avg_price"])) * qty - entry_fee - expected_fee
            if expected_policy != "T3"
            else -float(existing["avg_price"]) * qty - entry_fee
        ) + float(existing.get("realized_pnl_accumulator", 0.0))
        if not _strict_position_maps_equal(
            _mapping(row.get("positions_before"), "strict_replay_terminal_positions_before"),
            holdings,
        ):
            raise ValueError("strict_replay_terminal_positions_before")
        if (
            abs(fill_price - expected_price) > _EPS
            or abs(_number(row.get("fee"), "strict_replay_terminal_fee") - expected_fee) > _EPS
            or abs(_number(row.get("slippage_cost"), "strict_replay_terminal_slippage") - expected_slippage) > _EPS
            or abs(_number(row.get("spread_cost"), "strict_replay_terminal_spread") - expected_spread) > _EPS
            or abs(_number(row.get("proceeds"), "strict_replay_terminal_proceeds") - proceeds) > _EPS
            or abs(_number(row.get("cash_before"), "strict_replay_terminal_cash_before") - before_cash) > _EPS
            or row.get("position_removed") is not True
            or abs(_number(row.get("realized_pnl"), "strict_replay_terminal_realized_pnl") - expected_realized) > _EPS
            or abs(_number(row.get("pre_equity"), "strict_replay_terminal_pre_equity") - pre_equity) > _EPS
        ):
            raise ValueError("strict_replay_terminal_cost")
        cash += proceeds
        fees += expected_fee
        slippage_total += expected_slippage
        spread_total += expected_spread
        holdings.pop(symbol, None)
        if cash < -_EPS:
            raise ValueError("strict_replay_cash_negative")
        if abs(_number(row.get("cash_after"), "strict_replay_terminal_cash_after") - cash) > _EPS:
            raise ValueError("strict_replay_terminal_cash_chain")
        holdings_after = {symbol_name: value for symbol_name, value in holdings.items() if symbol_name != symbol}
        if not _strict_position_maps_equal(
            _mapping(row.get("positions_after"), "strict_replay_terminal_positions_after"),
            holdings_after,
        ):
            raise ValueError("strict_replay_terminal_positions_after")
        post_equity = cash + sum(
            float(position["qty"]) * mark_values.get(symbol_name, float(position["avg_price"]))
            for symbol_name, position in holdings_after.items()
        )
        if abs(_number(row.get("post_equity"), "strict_replay_terminal_post_equity") - post_equity) > _EPS:
            raise ValueError("strict_replay_terminal_equity_chain")
        enforce_fill_caps()
        state_history.append(
            (timestamp, cash, {symbol: dict(value) for symbol, value in holdings.items()}, fees)
        )
        # Terminal-aware engine snapshots are taken after in-cycle T1
        # removals.  FINAL_BAR liquidation is different: it runs after the
        # final holdings snapshot and only overwrites curve[-1].
        if expected_policy != "FINAL_BAR":
            metric_history.append(
                (timestamp, cash, {symbol_name: dict(value) for symbol_name, value in holdings.items()}, fees)
            )

    terminal_applied = terminal.get("applied") is True
    if terminal.get("applied") is not terminal_applied or terminal_applied is not bool(terminal_rows):
        raise ValueError("strict_replay_terminal_status")
    declared_terminal_symbols = terminal.get("liquidated_symbols")
    declared_terminal_symbol_set = (
        {str(value) for value in declared_terminal_symbols}
        if isinstance(declared_terminal_symbols, list)
        else set()
    )
    if (
        not isinstance(declared_terminal_symbols, list)
        or len(declared_terminal_symbols) != len(declared_terminal_symbol_set)
        or declared_terminal_symbol_set != terminal_seen_symbols
    ):
        raise ValueError("strict_replay_terminal_symbols")
    if terminal_applied and not terminal_seen_symbols:
        raise ValueError("strict_replay_terminal_coverage")
    if not terminal_applied and terminal_seen_symbols:
        raise ValueError("strict_replay_terminal_incomplete")
    if holdings:
        raise ValueError("strict_replay_terminal_incomplete")
    if abs(_number(terminal.get("cash_before"), "strict_replay_terminal_cash_before") - nonterminal_cash) > _EPS:
        raise ValueError("strict_replay_terminal_cash_before")
    if abs(_number(terminal.get("cash_after"), "strict_replay_terminal_cash_after") - cash) > _EPS:
        raise ValueError("strict_replay_terminal_cash_after")
    terminal_fee = sum(_number(row.get("fee"), "strict_replay_terminal_fee") for row in terminal_rows)
    terminal_slippage = sum(
        _number(row.get("slippage_cost"), "strict_replay_terminal_slippage") for row in terminal_rows
    )
    terminal_spread = sum(
        _number(row.get("spread_cost"), "strict_replay_terminal_spread") for row in terminal_rows
    )
    terminal_cash_delta = cash - nonterminal_cash
    if (
        abs(terminal_fee - _number(terminal.get("fee"), "strict_replay_terminal_fee_total")) > _EPS
        or abs(terminal_slippage - _number(terminal.get("slippage_cost"), "strict_replay_terminal_slippage_total"))
        > _EPS
        or abs(terminal_spread - _number(terminal.get("spread_cost"), "strict_replay_terminal_spread_total"))
        > _EPS
        or abs(terminal_cash_delta - _number(terminal.get("cash_delta"), "strict_replay_terminal_cash_delta")) > _EPS
    ):
        raise ValueError("strict_replay_terminal_summary")
    if abs(cash - _number(inner.get("cash"), "strict_replay_cash")) > _EPS:
        raise ValueError("strict_replay_cash")
    if abs(fees - _number(inner.get("fees"), "strict_replay_fees")) > _EPS:
        raise ValueError("strict_replay_fees")
    if abs(slippage_total - _number(inner.get("slippage_cost_total"), "strict_replay_slippage_total")) > _EPS:
        raise ValueError("strict_replay_slippage_total")
    if abs(spread_total - _number(inner.get("spread_cost_total"), "strict_replay_spread_total")) > _EPS:
        raise ValueError("strict_replay_spread_total")
    if abs(dividend_total - _number(inner.get("dividend_total"), "strict_replay_dividend_total")) > _EPS:
        raise ValueError("strict_replay_dividend_total")
    curve = [_number(value, "strict_replay_equity") for value in _sequence(inner.get("equity_curve"), "strict_replay_equity_curve")]
    curve_timestamps = [
        _timestamp(value, "strict_replay_equity_timestamp")
        for value in _sequence(inner.get("equity_curve_timestamps"), "strict_replay_equity_timestamps")
    ]
    if len(curve) != len(curve_timestamps) or not curve:
        raise ValueError("strict_replay_equity_curve")
    if any(current <= previous for previous, current in zip(curve_timestamps, curve_timestamps[1:], strict=False)):
        raise ValueError("strict_replay_equity_timestamp_order")
    if expected_curve_timestamps is not None and tuple(curve_timestamps) != tuple(expected_curve_timestamps):
        raise ValueError("strict_replay_executable_timestamp_binding")
    ordered_history = sorted(state_history, key=lambda item: item[0])
    mark_index = 0
    history_index = 0
    marks: dict[str, float] = {}
    current_cash = initial_cash
    current_fees = 0.0
    current_holdings: dict[str, dict[str, Any]] = {}
    risk_history: list[dict[str, object]] = []
    risk_session_date = None
    risk_day_start_equity = initial_cash
    risk_last_observed_equity = initial_cash
    risk_peak_equity = initial_cash
    new_york = ZoneInfo("America/New_York")
    for observed_timestamp, observed_equity in zip(curve_timestamps, curve, strict=True):
        while mark_index < len(ordered_source_bars) and ordered_source_bars[mark_index].timestamp <= observed_timestamp:
            source_bar = ordered_source_bars[mark_index]
            if not (str(source_bar.symbol) == "TLT" and source_bar.timestamp in marker_timestamps):
                marks[str(source_bar.symbol)] = float(source_bar.close)
            mark_index += 1
        while history_index < len(ordered_history) and ordered_history[history_index][0] <= observed_timestamp:
            _history_timestamp, current_cash, current_holdings, current_fees = ordered_history[history_index]
            history_index += 1
        expected_equity = current_cash + sum(
            float(position["qty"]) * marks.get(symbol, float(position["avg_price"]))
            for symbol, position in current_holdings.items()
        )
        if abs(observed_equity - expected_equity) > _EPS:
            raise ValueError("strict_replay_equity_curve")
        session_date = observed_timestamp.astimezone(new_york).date()
        if risk_session_date != session_date:
            risk_day_start_equity = risk_last_observed_equity
            risk_session_date = session_date
        risk_last_observed_equity = expected_equity
        risk_peak_equity = max(risk_peak_equity, expected_equity)
        risk_history.append(
            {
                "timestamp": observed_timestamp.isoformat(),
                "equity": expected_equity,
                "day_start_equity": risk_day_start_equity,
                "daily_pnl": expected_equity - risk_day_start_equity,
                "peak_equity": risk_peak_equity,
                "drawdown_pct": max(
                    0.0,
                    (risk_peak_equity - expected_equity) / risk_peak_equity,
                ),
            }
        )
    snapshots = _sequence(inner.get("snapshots"), "strict_replay_snapshots")
    if not snapshots:
        raise ValueError("strict_replay_snapshots_missing")
    snapshot_timestamps = [
        _timestamp(_mapping(raw_snapshot, "strict_replay_snapshot").get("timestamp"), "strict_replay_snapshot_timestamp")
        for raw_snapshot in snapshots
    ]
    if snapshot_timestamps != curve_timestamps:
        raise ValueError("strict_replay_snapshot_timestamp_binding")
    previous_snapshot_timestamp: datetime | None = None
    for raw_snapshot in snapshots:
        snapshot = _mapping(raw_snapshot, "strict_replay_snapshot")
        snapshot_timestamp = _timestamp(snapshot.get("timestamp"), "strict_replay_snapshot_timestamp")
        if previous_snapshot_timestamp is not None and snapshot_timestamp <= previous_snapshot_timestamp:
            raise ValueError("strict_replay_snapshot_order")
        previous_snapshot_timestamp = snapshot_timestamp
        snapshot_marks = {
            str(source_bar.symbol): float(source_bar.close)
            for source_bar in ordered_source_bars
            if source_bar.timestamp <= snapshot_timestamp
        }
        snapshot_cash = initial_cash
        snapshot_fees = 0.0
        snapshot_holdings: dict[str, dict[str, Any]] = {}
        for history_timestamp, history_cash, history_holdings, history_fees in ordered_history:
            if history_timestamp <= snapshot_timestamp:
                snapshot_cash, snapshot_holdings, snapshot_fees = history_cash, history_holdings, history_fees
            else:
                break
        snapshot_positions = _mapping(snapshot.get("positions"), "strict_replay_snapshot_positions")
        if snapshot.get("realized_holdings") != snapshot.get("positions"):
            raise ValueError("strict_replay_snapshot_realized_holdings")
        snapshot_equity = snapshot_cash + sum(
            float(position["qty"]) * snapshot_marks.get(symbol, float(position["avg_price"]))
            for symbol, position in snapshot_holdings.items()
        )
        if abs(_number(snapshot.get("cash"), "strict_replay_snapshot_cash") - snapshot_cash) > _EPS:
            raise ValueError("strict_replay_snapshot_cash")
        if abs(_number(snapshot.get("equity"), "strict_replay_snapshot_equity") - snapshot_equity) > _EPS:
            raise ValueError("strict_replay_snapshot_equity")
        if abs(_number(snapshot.get("fees"), "strict_replay_snapshot_fees") - snapshot_fees) > _EPS:
            raise ValueError("strict_replay_snapshot_fees")
        invested = 0.0
        for symbol, raw_position in snapshot_positions.items():
            position = _mapping(raw_position, "strict_replay_snapshot_position")
            expected = snapshot_holdings.get(symbol)
            if expected is None:
                raise ValueError("strict_replay_snapshot_positions")
            mark = snapshot_marks.get(symbol)
            if mark is None or abs(_number(position.get("mark"), "strict_replay_snapshot_mark") - mark) > _EPS:
                raise ValueError("strict_replay_snapshot_mark")
            qty = _number(position.get("qty"), "strict_replay_snapshot_qty")
            value = _number(position.get("value"), "strict_replay_snapshot_value")
            if abs(qty - float(expected["qty"])) > _EPS or abs(value - qty * mark) > _EPS:
                raise ValueError("strict_replay_snapshot_position")
            invested += value
        if set(snapshot_positions) != set(snapshot_holdings):
            raise ValueError("strict_replay_snapshot_positions")
        expected_gross = invested / snapshot_equity if snapshot_equity > 0 else 0.0
        expected_cash_weight = snapshot_cash / snapshot_equity if snapshot_equity > 0 else 0.0
        if abs(_number(snapshot.get("gross_exposure"), "strict_replay_snapshot_gross") - expected_gross) > _EPS:
            raise ValueError("strict_replay_snapshot_gross")
        if abs(_number(snapshot.get("cash_weight"), "strict_replay_snapshot_cash_weight") - expected_cash_weight) > _EPS:
            raise ValueError("strict_replay_snapshot_cash_weight")
    observed_positions = _mapping(inner.get("positions"), "strict_replay_positions")
    if set(observed_positions) != set(holdings):
        raise ValueError("strict_replay_positions")
    for symbol, expected in holdings.items():
        observed = _mapping(observed_positions.get(symbol), "strict_replay_position")
        if (
            abs(_number(observed.get("qty"), "strict_replay_position_qty") - float(expected["qty"])) > _EPS
            or abs(_number(observed.get("avg_price"), "strict_replay_avg_price") - float(expected["avg_price"])) > _EPS
            or observed.get("owner") != expected["owner"]
        ):
            raise ValueError("strict_replay_positions")
    if inner.get("realized_holdings") != inner.get("positions"):
        raise ValueError("strict_replay_realized_holdings")

    # Derive holdings/cash/concentration observations from replay state and
    # trusted as-of marks.  FINAL_BAR rows are absent so end-only liquidation
    # cannot rewrite the engine's holdings audit.
    metric_observations: list[dict[str, object]] = []
    ordered_metric_history = sorted(metric_history, key=lambda item: item[0])
    metric_index = 0
    metric_cash = initial_cash
    metric_holdings: dict[str, dict[str, Any]] = {}
    for curve_timestamp in curve_timestamps:
        while metric_index < len(ordered_metric_history) and ordered_metric_history[metric_index][0] <= curve_timestamp:
            _metric_timestamp, metric_cash, metric_holdings, _metric_fees = ordered_metric_history[metric_index]
            metric_index += 1
        metric_marks: dict[str, float] = {}
        for source_bar in ordered_source_bars:
            if source_bar.timestamp > curve_timestamp:
                break
            metric_marks[str(source_bar.symbol)] = float(source_bar.close)
        metric_equity = metric_cash + sum(
            float(position["qty"]) * metric_marks.get(symbol, float(position["avg_price"]))
            for symbol, position in metric_holdings.items()
        )
        if metric_equity <= 0:
            raise ValueError("strict_replay_metric_equity_nonpositive")
        invested_value = sum(
            float(position["qty"]) * metric_marks.get(symbol, float(position["avg_price"]))
            for symbol, position in metric_holdings.items()
        )
        concentration = max(
            (
                float(position["qty"])
                * metric_marks.get(symbol, float(position["avg_price"]))
                / metric_equity
                for symbol, position in metric_holdings.items()
            ),
            default=0.0,
        )
        metric_observations.append(
            {
                "timestamp": curve_timestamp.isoformat(),
                "holdings_count": len(metric_holdings),
                "cash_ratio": max(0.0, (metric_equity - invested_value) / metric_equity),
                "maximum_concentration": concentration,
            }
        )
    trusted_metrics: dict[str, object] = {
        "average_holdings_count": sum(_number(row["holdings_count"], "strict_metric_holdings_count") for row in metric_observations)
        / len(metric_observations)
        if metric_observations
        else 0.0,
        "average_cash_ratio": sum(_number(row["cash_ratio"], "strict_metric_cash_ratio") for row in metric_observations)
        / len(metric_observations)
        if metric_observations
        else 0.0,
        "maximum_concentration": max(
            (_number(row["maximum_concentration"], "strict_metric_concentration") for row in metric_observations),
            default=0.0,
        ),
        "fill_count": len(fills),
        "closed_positions": closed_positions + len(terminal_rows),
        "rebalance_count": len(_sequence(inner.get("rebalances", ()), "strict_replay_rebalances")),
        "fee_total": fees,
        "slippage_cost_total": slippage_total,
        "spread_cost_total": spread_total,
        "dividend_total": dividend_total,
    }
    return {
        "cash": cash,
        "fees": fees,
        "slippage_cost_total": slippage_total,
        "spread_cost_total": spread_total,
        "dividend_total": dividend_total,
        "holdings": {symbol: dict(value) for symbol, value in holdings.items()},
        "state_history": state_history,
        "risk_history": risk_history,
        "metric_observations": metric_observations,
        "trusted_metrics": trusted_metrics,
        "fills": [dict(_mapping(raw_fill, "strict_replay_fill")) for raw_fill in fills],
        "terminal_rows": [dict(_mapping(raw_row, "strict_replay_terminal_row")) for raw_row in terminal_rows],
    }


_STRICT_MIN_HISTORY_BARS = 274
_STRICT_CANDIDATE_WEIGHT = 0.025
_STRICT_CANDIDATE_VERSION = "2.0.0"
_STRICT_INCUMBENT_NAME = "equity_residual_momentum_v3"
_STRICT_INCUMBENT_VERSION = "3.0.0"


def _strict_expected_decision_clock(
    *,
    curve_timestamps: Sequence[datetime],
    bars: Mapping[tuple[str, datetime], Any],
    marker_timestamps: set[datetime],
    event_boundaries: set[datetime],
    candidate: bool,
    ordered_bars: Sequence[Any] | None = None,
) -> tuple[datetime, ...]:
    """Derive the engine's rebalance clock from trusted source timestamps.

    ``RebalancePolicy`` uses the first bar of each ISO week.  The R46
    candidate adds event boundaries, then restores the prior weekly cursor so
    a marker cannot consume a V3 cadence point.  This is a small replay of
    those clock semantics only; it never instantiates or runs a strategy.
    """

    ordered_curve = tuple(curve_timestamps)
    if any(current <= previous for previous, current in zip(ordered_curve, ordered_curve[1:], strict=False)):
        raise ValueError("strict_decision_clock_order")
    if candidate and not event_boundaries.issubset(set(ordered_curve)):
        raise ValueError("strict_event_decision_timestamp_missing")
    ordered_bars = tuple(ordered_bars) if ordered_bars is not None else tuple(
        sorted(bars.values(), key=lambda bar: (bar.timestamp, str(bar.symbol)))
    )
    history_counts: dict[str, int] = {}
    source_index = 0
    previous: datetime | None = None
    expected: list[datetime] = []
    for current in ordered_curve:
        while source_index < len(ordered_bars) and ordered_bars[source_index].timestamp <= current:
            source_bar = ordered_bars[source_index]
            if not (str(source_bar.symbol) == "TLT" and source_bar.timestamp in marker_timestamps):
                symbol = str(source_bar.symbol)
                history_counts[symbol] = history_counts.get(symbol, 0) + 1
            source_index += 1
        # A control marker is an executable TLT bar with no incumbent batch.
        # When a shifted marker lands on the weekly boundary, the native
        # control arm records the same skipped incomplete batch seen in the
        # source replay.  Candidate event boundaries are handled below and do
        # not take this skip path.
        if not candidate and current in marker_timestamps:
            previous = current
            continue
        event_boundary = candidate and current in event_boundaries
        weekly_boundary = previous is None or previous.isocalendar()[:2] != current.isocalendar()[:2]
        if (event_boundary or weekly_boundary) and max(history_counts.values(), default=0) >= _STRICT_MIN_HISTORY_BARS:
            expected.append(current)
        # _R46PortfolioBacktestEngine restores the pre-event timestamp after
        # every event boundary, including a boundary before warmup.  Keep the
        # old cursor for the next cycle to match that behavior exactly.
        if not event_boundary:
            previous = current
    return tuple(expected)


def _strict_replay_state_at(
    *,
    inner: Mapping[str, Any],
    replay: Mapping[str, Any],
    bars: Mapping[tuple[str, datetime], Any],
    timestamp: datetime,
    marker_timestamps: set[datetime],
    ordered_bars: Sequence[Any] | None = None,
) -> tuple[float, dict[str, dict[str, Any]], dict[str, float], float, float | None]:
    """Return trusted cash, holdings, marks, equity, and real TLT reference."""

    cash = _number(inner.get("initial_cash"), "strict_decision_initial_cash")
    holdings: dict[str, dict[str, Any]] = {}
    for raw_state in replay.get("state_history", ()):
        if not isinstance(raw_state, (tuple, list)) or len(raw_state) != 4:
            raise ValueError("strict_decision_state_history")
        state_timestamp = _timestamp(raw_state[0], "strict_decision_state_timestamp")
        if state_timestamp > timestamp:
            break
        cash = _number(raw_state[1], "strict_decision_state_cash")
        holdings = {
            str(symbol): dict(_mapping(position, "strict_decision_state_position"))
            for symbol, position in _mapping(raw_state[2], "strict_decision_state_holdings").items()
        }
    marks: dict[str, float] = {}
    ordered_bars = tuple(ordered_bars) if ordered_bars is not None else tuple(
        sorted(bars.values(), key=lambda bar: (bar.timestamp, str(bar.symbol)))
    )
    for bar in ordered_bars:
        if bar.timestamp > timestamp:
            break
        if str(bar.symbol) == "TLT" and bar.timestamp in marker_timestamps:
            continue
        marks[str(bar.symbol)] = float(bar.close)
    equity = cash + sum(
        float(position["qty"]) * marks.get(symbol, float(position["avg_price"]))
        for symbol, position in holdings.items()
    )
    real_tlt_reference: float | None = None
    for bar in ordered_bars:
        if bar.timestamp > timestamp:
            break
        if str(bar.symbol) == "TLT" and bar.timestamp not in marker_timestamps:
            real_tlt_reference = float(bar.close)
    return cash, holdings, marks, equity, real_tlt_reference


def _strict_replay_risk_at(
    replay: Mapping[str, Any], timestamp: datetime
) -> Mapping[str, Any]:
    """Return the independently reconstructed per-bar risk state at ``timestamp``."""

    selected: Mapping[str, Any] | None = None
    for raw_state in _sequence(replay.get("risk_history", ()), "strict_decision_risk_history"):
        state = _mapping(raw_state, "strict_decision_risk_state")
        state_timestamp = _timestamp(state.get("timestamp"), "strict_decision_risk_timestamp")
        if state_timestamp > timestamp:
            break
        selected = state
    if selected is None:
        raise ValueError("strict_decision_risk_state_missing")
    return selected


def _strict_position_maps_equal(
    observed: Mapping[str, Any], expected: Mapping[str, Any]
) -> bool:
    """Compare replay position snapshots with numeric tolerance only.

    The engine and the verifier may associate a sequence of fills in a
    different but equivalent floating-point grouping order.  Identity/owner
    fields remain exact while the serialized numeric quantities use the same
    tolerance as every other strict replay field.
    """

    if set(observed) != set(expected):
        return False
    numeric_fields = {
        "qty",
        "avg_price",
        "entry_fee",
        "realized_pnl_accumulator",
    }
    for symbol in expected:
        observed_position = _mapping(observed[symbol], "strict_position_observed")
        expected_position = _mapping(expected[symbol], "strict_position_expected")
        if set(observed_position) != set(expected_position):
            return False
        for field in expected_position:
            if field in numeric_fields:
                try:
                    if abs(float(observed_position[field]) - float(expected_position[field])) > _EPS:
                        return False
                except (TypeError, ValueError):
                    return False
            elif observed_position[field] != expected_position[field]:
                return False
    return True


def _strict_pair_map(raw: object, field: str) -> dict[str, float]:
    """Parse a serialized ``((symbol, value), ...)`` engine tuple exactly."""

    result: dict[str, float] = {}
    for raw_pair in _sequence(raw, field):
        pair = _sequence(raw_pair, f"{field}_pair")
        if len(pair) != 2:
            raise ValueError(f"{field}_pair")
        symbol = str(pair[0])
        if not symbol or symbol in result:
            raise ValueError(f"{field}_identity")
        result[symbol] = _number(pair[1], f"{field}_value")
    return result


def _strict_validate_rebalance_details(
    *,
    inner: Mapping[str, Any],
    replay: Mapping[str, Any],
    bars: Mapping[tuple[str, datetime], Any],
    marker_timestamps: set[datetime],
    decision_contract: Mapping[str, Any],
    candidate: bool,
    turnover_band: float,
    minimum_order_notional: float,
    oracle_contract: Mapping[str, Any] | None = None,
) -> None:
    """Bind every detailed rebalance accounting field to trusted replay.

    The native engine's candidate records intentionally use two equity
    denominators: ordinary V3 rows exclude the R46 sleeve, while event rows
    size TLT against the full account.  Reconstruct that small accounting
    distinction here from replay state; no serialized rebalance field is an
    authority input.
    """

    expected_timestamps = tuple(cast(Sequence[datetime], decision_contract["expected_timestamps"]))
    target_maps = cast(Mapping[datetime, dict[str, float]], decision_contract["target_maps"])
    event_boundaries = cast(set[datetime], decision_contract["event_boundaries"])
    rebalances = [_mapping(raw, "strict_rebalance_detail") for raw in _sequence(inner.get("rebalances"), "strict_rebalances")]
    fills = [_mapping(raw, "strict_rebalance_fill") for raw in _sequence(inner.get("fills"), "strict_rebalance_fills")]
    oracle_ordered_bars = (
        oracle_contract.get("oracle_ordered_bars")
        if oracle_contract is not None
        else None
    )
    fills_by_decision: dict[datetime, list[Mapping[str, Any]]] = {}
    for fill in fills:
        decision_timestamp = _timestamp(fill.get("decision_timestamp"), "strict_rebalance_fill_decision")
        fills_by_decision.setdefault(decision_timestamp, []).append(fill)

    # TLT dividends are removed from the incumbent-equity denominator exactly
    # as _R46PortfolioBacktestEngine._incumbent_equity does.
    tlt_dividends: list[tuple[datetime, float]] = []
    for raw_event in _sequence(inner.get("dividend_events", ()), "strict_rebalance_dividends"):
        event = _mapping(raw_event, "strict_rebalance_dividend")
        if str(event.get("symbol", "")) == "TLT":
            tlt_dividends.append(
                (_timestamp(event.get("timestamp"), "strict_rebalance_dividend_timestamp"), _number(event.get("credited_cash", 0.0), "strict_rebalance_dividend_cash"))
            )

    for timestamp, raw_rebalance in zip(expected_timestamps, rebalances, strict=True):
        _cash, holdings, marks, full_equity, _reference = _strict_replay_state_at(
            inner=inner,
            replay=replay,
            bars=bars,
            timestamp=timestamp,
            marker_timestamps=marker_timestamps,
            ordered_bars=cast(Sequence[Any] | None, oracle_ordered_bars),
        )
        if full_equity <= 0:
            raise ValueError("strict_rebalance_equity")
        tlt_position = holdings.get("TLT")
        tlt_mark = marks.get("TLT", float(tlt_position["avg_price"]) if tlt_position else 0.0)
        tlt_value = float(tlt_position["qty"]) * tlt_mark if tlt_position else 0.0
        overlay_cash_delta = 0.0
        for raw_fill in fills:
            if str(raw_fill.get("symbol", "")) != "TLT":
                continue
            fill_timestamp = _timestamp(raw_fill.get("timestamp"), "strict_rebalance_fill_timestamp")
            if fill_timestamp > timestamp:
                continue
            qty = _number(raw_fill.get("qty"), "strict_rebalance_fill_qty")
            price = _number(raw_fill.get("price"), "strict_rebalance_fill_price")
            fee = _number(raw_fill.get("fee"), "strict_rebalance_fill_fee")
            if str(raw_fill.get("side", "")).upper() == "BUY":
                overlay_cash_delta += qty * price + fee
            elif str(raw_fill.get("side", "")).upper() == "SELL":
                overlay_cash_delta -= qty * price - fee
            else:
                raise ValueError("strict_rebalance_fill_side")
        overlay_cash_delta -= sum(
            value for event_timestamp, value in tlt_dividends if event_timestamp <= timestamp
        )
        incumbent_equity = full_equity + overlay_cash_delta - tlt_value
        if incumbent_equity <= 0:
            raise ValueError("strict_rebalance_incumbent_equity")
        event_row = candidate and timestamp in event_boundaries
        oracle_target_maps = (
            cast(Mapping[datetime, dict[str, float]], oracle_contract["oracle_target_maps"])
            if oracle_contract is not None and "oracle_target_maps" in oracle_contract
            else target_maps
        )
        target_map = oracle_target_maps[timestamp]
        # The event path sizes only TLT; ordinary candidate rows normalize the
        # public incumbent record and exclude TLT from its detailed fields.
        symbols = set(target_map) | set(holdings)
        if candidate and not event_row:
            symbols.discard("TLT")

        def denominator(symbol: str) -> float:
            if candidate and event_row and symbol == "TLT":
                return full_equity
            return incumbent_equity if candidate else full_equity

        pre_trade = {
            symbol: float(holdings.get(symbol, {}).get("qty", 0.0))
            * marks.get(symbol, float(holdings.get(symbol, {}).get("avg_price", 0.0)))
            / denominator(symbol)
            for symbol in symbols
        }
        order_totals: dict[tuple[str, str, str], float] = {}
        for fill in fills_by_decision.get(timestamp, ()):
            order_id = str(fill.get("client_order_id", ""))
            symbol = str(fill.get("symbol", ""))
            side = str(fill.get("side", "")).upper()
            qty = _number(fill.get("qty"), "strict_rebalance_fill_qty")
            order_totals[(order_id, symbol, side)] = order_totals.get((order_id, symbol, side), 0.0) + qty
        for (_order_id, symbol, side), filled_qty in order_totals.items():
            target_weight = target_map.get(symbol, 0.0)
            current_weight = pre_trade.get(symbol, 0.0)
            price = marks.get(symbol, float(holdings.get(symbol, {}).get("avg_price", 0.0)))
            equity = denominator(symbol)
            current_qty = float(holdings.get(symbol, {}).get("qty", 0.0))
            target_qty = target_weight * equity / price if price > 0 else 0.0
            delta = target_qty - current_qty
            order_required = (
                price > 0
                and not (candidate and event_row and symbol != "TLT")
                and not (candidate and not event_row and symbol == "TLT")
                and abs(target_weight - current_weight) >= turnover_band
                and abs(delta) * price >= minimum_order_notional
                and ((side == "BUY" and delta > 0) or (side == "SELL" and delta < 0))
            )
            if not order_required:
                raise ValueError("strict_rebalance_unexpected_fill")
            if filled_qty > abs(delta) + _EPS:
                raise ValueError("strict_rebalance_order_overfill")
        accounted_fills = [
            fill
            for fill in fills_by_decision.get(timestamp, ())
            if not (candidate and not event_row and str(fill.get("symbol")) == "TLT")
        ]
        signed_trade: dict[str, float] = {}
        signed_notional: dict[str, float] = {}
        executed: dict[str, float] = {}
        cost = 0.0
        for fill in accounted_fills:
            symbol = str(fill.get("symbol", ""))
            qty = _number(fill.get("qty"), "strict_rebalance_fill_qty")
            price = _number(fill.get("price"), "strict_rebalance_fill_price")
            signed = qty * price * (1.0 if str(fill.get("side", "")).upper() == "BUY" else -1.0)
            signed_notional[symbol] = signed_notional.get(symbol, 0.0) + signed
            signed_trade[symbol] = signed_trade.get(symbol, 0.0) + signed / denominator(symbol)
            executed[symbol] = executed.get(symbol, 0.0) + qty * price / denominator(symbol)
            cost += sum(
                _number(fill.get(field), f"strict_rebalance_{field}")
                for field in ("slippage_cost", "spread_cost", "fee")
            )
        actual = {symbol: pre_trade.get(symbol, 0.0) + value for symbol, value in signed_trade.items()}
        drift_symbols = set(target_map) | set(signed_trade)
        drift = max(
            (
                abs(target_map.get(symbol, 0.0) - actual.get(symbol, pre_trade.get(symbol, 0.0)))
                for symbol in drift_symbols
            ),
            default=0.0,
        )
        expected_notional = 0.0
        for symbol, target_weight in target_map.items():
            current_weight = pre_trade.get(symbol, 0.0)
            expected_notional += abs(target_weight - current_weight) * full_equity if not candidate or event_row else abs(target_weight - current_weight) * incumbent_equity
        expected_turnover = 0.0
        if candidate and not event_row:
            expected_turnover = expected_notional / incumbent_equity
        else:
            order_symbols = set(target_map) | set(holdings)
            for symbol in order_symbols:
                target_weight = target_map.get(symbol, 0.0)
                current_weight = pre_trade.get(symbol, 0.0)
                if candidate and event_row and symbol != "TLT":
                    continue
                if candidate and not event_row and symbol == "TLT":
                    continue
                price = marks.get(symbol, float(holdings.get(symbol, {}).get("avg_price", 0.0)))
                equity = denominator(symbol)
                current_qty = float(holdings.get(symbol, {}).get("qty", 0.0))
                target_qty = target_weight * equity / price if price > 0 else 0.0
                if (
                    price > 0
                    and abs(target_weight - current_weight) >= turnover_band
                    and abs(target_qty - current_qty) * price >= minimum_order_notional
                ):
                    expected_turnover += abs(target_weight - current_weight)
        sold = {symbol for symbol in signed_trade if symbol not in target_map}
        expected_notional += sum(abs(pre_trade.get(symbol, 0.0)) * (full_equity if not candidate or event_row else incumbent_equity) for symbol in sold)
        realized_notional = sum(abs(value) for value in signed_notional.values())
        expected_unfilled = max(0.0, expected_notional - realized_notional)
        expected_equity = full_equity if not candidate or event_row else incumbent_equity
        expected_executed = tuple(sorted(executed.items()))
        expected_signed = tuple(sorted(signed_trade.items()))
        expected_post = tuple(sorted(actual.items()))
        if abs(_number(raw_rebalance.get("equity_at_decision"), "strict_rebalance_equity_at_decision") - expected_equity) > _EPS:
            raise ValueError("strict_rebalance_equity_at_decision")
        if _strict_pair_map(raw_rebalance.get("pre_trade_weights"), "strict_rebalance_pre_trade") != pre_trade:
            observed = _strict_pair_map(raw_rebalance.get("pre_trade_weights"), "strict_rebalance_pre_trade")
            if set(observed) != set(pre_trade) or any(abs(observed[symbol] - pre_trade[symbol]) > _EPS for symbol in pre_trade):
                raise ValueError("strict_rebalance_pre_trade")
        for field, expected_pairs in (
            ("signed_trade_weights", expected_signed),
            ("post_fill_actual_weights", expected_post),
            ("executed_weights", expected_executed),
        ):
            observed = _strict_pair_map(raw_rebalance.get(field), f"strict_rebalance_{field}")
            expected_map = dict(expected_pairs)
            if set(observed) != set(expected_map) or any(abs(observed[symbol] - expected_map[symbol]) > _EPS for symbol in expected_map):
                raise ValueError(f"strict_rebalance_{field}")
        checks: tuple[tuple[str, float], ...] = (
            ("target_vs_actual_drift", drift),
            ("unfilled_notional", expected_unfilled),
            ("rejected_notional", 0.0),
            ("expected_turnover", expected_turnover),
            ("realized_turnover", sum(abs(value) for _symbol, value in expected_signed)),
            ("cost", cost),
        )
        for field, expected_value in checks:
            if abs(_number(raw_rebalance.get(field), f"strict_rebalance_{field}") - expected_value) > _EPS:
                raise ValueError(f"strict_rebalance_{field}")


def _strict_decision_target_map(
    decision: Mapping[str, Any], *, field: str
) -> tuple[dict[str, float], dict[str, str]]:
    target_map: dict[str, float] = {}
    owner_map: dict[str, str] = {}
    target_field = "target_weights" if field == "strict_decision" else "desired_target_weights"
    for raw_target in _sequence(decision.get(target_field), f"{field}_target_weights"):
        target = _mapping(raw_target, f"{field}_target_weight")
        symbol = str(target.get("symbol", ""))
        owner = str(target.get("owner", ""))
        if not symbol or symbol in target_map or not owner:
            raise ValueError("strict_decision_target_identity")
        expected_owner = "r46" if symbol == "TLT" else "incumbent_v3"
        expected_reason = "r46_event_episode" if symbol == "TLT" else "unchanged_incumbent_v3"
        if owner != expected_owner or target.get("reason") != expected_reason:
            raise ValueError("strict_decision_target_attribution")
        value = _number(target.get("weight"), f"{field}_target_weight_value")
        if value < -_EPS or value > 1.0 + _EPS:
            raise ValueError("strict_decision_target_weight")
        target_map[symbol] = value
        owner_map[symbol] = owner
    return target_map, owner_map


def _strict_rebalance_target_map(raw_rebalance: Mapping[str, Any]) -> dict[str, float]:
    target_map: dict[str, float] = {}
    for raw_target in _sequence(raw_rebalance.get("target_weights"), "strict_rebalance_target_weights"):
        target = _sequence(raw_target, "strict_rebalance_target_weight")
        if len(target) != 2:
            raise ValueError("strict_rebalance_target_weight")
        symbol = str(target[0])
        if not symbol or symbol in target_map:
            raise ValueError("strict_rebalance_target_identity")
        target_map[symbol] = _number(target[1], "strict_rebalance_target_weight_value")
    return target_map


def _strict_validate_decision_clock(
    *,
    inner: Mapping[str, Any],
    replay: dict[str, Any],
    bars: Mapping[tuple[str, datetime], Any],
    marker_timestamps: set[datetime],
    event_boundaries: set[datetime],
    expected_curve_timestamps: Sequence[datetime] | None,
    candidate: bool,
    turnover_band: float = 0.005,
    minimum_order_notional: float = 10.0,
    oracle_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    curve_timestamps = tuple(
        _timestamp(value, "strict_decision_curve_timestamp")
        for value in _sequence(inner.get("equity_curve_timestamps"), "strict_decision_curve_timestamps")
    )
    if expected_curve_timestamps is not None and curve_timestamps != tuple(expected_curve_timestamps):
        raise ValueError("strict_decision_curve_binding")
    expected = _strict_expected_decision_clock(
        curve_timestamps=curve_timestamps,
        bars=bars,
        marker_timestamps=marker_timestamps,
        event_boundaries=event_boundaries,
        candidate=candidate,
        ordered_bars=(oracle_context.get("ordered_bars") if oracle_context is not None else None),
    )
    rebalances = [_mapping(raw, "strict_rebalance") for raw in _sequence(inner.get("rebalances"), "strict_rebalances")]
    decisions = [_mapping(raw, "strict_decision") for raw in _sequence(inner.get("decisions"), "strict_decisions")]
    if len(rebalances) != len(expected) or len(decisions) != len(expected):
        raise ValueError("strict_rebalance_clock_completeness")
    expected_numbers: list[int] = []
    rebalance_number = 0
    for timestamp in expected:
        rebalance_number += 1
        expected_numbers.append(rebalance_number)
        if candidate and timestamp in event_boundaries:
            rebalance_number -= 1
    decision_by_timestamp: dict[datetime, Mapping[str, Any]] = {}
    rebalance_by_timestamp: dict[datetime, Mapping[str, Any]] = {}
    target_maps: dict[datetime, dict[str, float]] = {}
    desired_target_maps: dict[datetime, dict[str, float]] = {}
    oracle_target_maps: dict[datetime, dict[str, float]] = {}
    oracle_desired_target_maps: dict[datetime, dict[str, float]] = {}
    oracle_accepted_ids: set[str] = set()
    oracle_session: CachedV3TargetOracle | None = None
    oracle_schedules: tuple[Any, ...] = ()
    oracle_ordered_bars: Sequence[Any] | None = None
    oracle_maximum_allocation = 0.55
    oracle_pending: PendingOrderLedger | None = None
    if oracle_context is not None:
        try:
            oracle_session = CachedV3TargetOracle(
                bars=oracle_context["bars"],
                _groups=oracle_context.get("_groups"),
                _timestamp_groups=oracle_context.get("_timestamp_groups"),
                _target_cache=oracle_context.get("_target_cache"),
                universe=oracle_context.get("universe"),
                terminal_map=oracle_context.get("terminal_map"),
            )
            oracle_schedules = tuple(oracle_context.get("schedules", ()))
            oracle_ordered_bars = oracle_context.get("ordered_bars")
            oracle_maximum_allocation = float(oracle_context.get("maximum_allocation", 0.55))
            oracle_expiry_by_request = {
                **{
                    (getattr(schedule, "entry_decision"), "TLT", "BUY"): getattr(schedule, "entry_open")
                    for schedule in oracle_schedules
                },
                **{
                    (getattr(schedule, "exit_decision"), "TLT", "SELL"): getattr(schedule, "exit_open")
                    for schedule in oracle_schedules
                },
            }
            oracle_pending = PendingOrderLedger(
                fills=tuple(
                    _mapping(raw_fill, "strict_oracle_pending_fill")
                    for raw_fill in _sequence(replay.get("fills", ()), "strict_oracle_pending_fills")
                ),
                candidate=candidate,
                event_boundaries=tuple(event_boundaries),
                expiry_by_request=oracle_expiry_by_request,
                turnover_band=turnover_band,
                minimum_order_notional=minimum_order_notional,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("strict_v3_oracle_context") from exc
    schedule_by_id = {str(getattr(schedule, "event_id")): schedule for schedule in oracle_schedules}

    def oracle_expected(
        timestamp: datetime,
        *,
        event_boundary: bool,
        rebalance_number: int,
    ) -> tuple[dict[str, float], dict[str, float]]:
        if oracle_session is None:
            return {}, {}
        try:
            state_snapshot: tuple[float, dict[str, dict[str, Any]], dict[str, float], float, float | None] | None = None

            def state_at_timestamp() -> tuple[float, dict[str, dict[str, Any]], dict[str, float], float, float | None]:
                nonlocal state_snapshot
                if state_snapshot is None:
                    state_snapshot = _strict_replay_state_at(
                        inner=inner,
                        replay=replay,
                        bars=bars,
                        timestamp=timestamp,
                        marker_timestamps=marker_timestamps,
                        ordered_bars=oracle_ordered_bars,
                    )
                return state_snapshot

            # The production observation builder reserves managed holdings and
            # the independently reconstructed remainder of prior non-TLT
            # orders before taking the PIT ranked tail.  Event boundaries
            # deliberately reuse the already-cached ordinary target without
            # recomputation.
            pending_symbols = (
                oracle_pending.pending_symbols(timestamp) if oracle_pending is not None else ()
            )
            if event_boundary:
                base = oracle_session.target(timestamp, event_boundary=True)
            else:
                _cash, holdings, _marks, _equity, _reference = state_at_timestamp()
                managed_symbols = tuple(
                    sorted(
                        str(symbol).upper()
                        for symbol, position in holdings.items()
                        if float(position.get("qty", 0.0)) > _EPS
                    )
                )
                base = oracle_session.target(
                    timestamp,
                    managed_symbols=managed_symbols,
                    pending_symbols=pending_symbols,
                )

            if candidate:
                entry_schedules = [
                    schedule for schedule in oracle_schedules if getattr(schedule, "entry_decision", None) == timestamp
                ]
                for schedule in entry_schedules:
                    event_id = str(getattr(schedule, "event_id"))
                    if event_id in oracle_accepted_ids:
                        continue
                    already_active = any(
                        accepted_id in oracle_accepted_ids
                        and getattr(schedule_by_id[accepted_id], "entry_decision") <= timestamp < getattr(schedule_by_id[accepted_id], "exit_decision")
                        for accepted_id in oracle_accepted_ids
                    )
                    _cash, holdings, _marks, _equity, _reference = state_at_timestamp()
                    carried = {symbol for symbol, position in holdings.items() if float(position.get("qty", 0.0)) > _EPS}
                    projected = carried | set(base.weight_map) | {"TLT"}
                    if (
                        not already_active
                        and len(projected) <= int(FROZEN_CAPS["total_positions_max"])
                        and sum(base.weight_map.values()) + _STRICT_CANDIDATE_WEIGHT
                        <= float(FROZEN_CAPS["combined_gross_max"]) + _EPS
                        and base.cash_weight - _STRICT_CANDIDATE_WEIGHT
                        >= float(FROZEN_CAPS["combined_cash_min"]) - _EPS
                    ):
                        oracle_accepted_ids.add(event_id)
                active = any(
                    event_id in oracle_accepted_ids
                    and getattr(schedule, "entry_decision") <= timestamp < getattr(schedule, "exit_decision")
                    for event_id, schedule in schedule_by_id.items()
                )
            else:
                active = False
            desired = add_overlay_weight(base, "TLT", _STRICT_CANDIDATE_WEIGHT) if candidate and active else base
            state_cash, state_holdings, state_marks, state_equity, _reference = state_at_timestamp()
            risk_snapshot = _strict_replay_risk_at(replay, timestamp)
            risk_state = {
                "cash": state_cash,
                "holdings": state_holdings,
                "marks": state_marks,
                "equity": state_equity,
                "daily_pnl": _number(risk_snapshot.get("daily_pnl"), "strict_oracle_daily_pnl"),
                "peak_equity": _number(risk_snapshot.get("peak_equity"), "strict_oracle_peak_equity"),
                "drawdown_pct": _number(risk_snapshot.get("drawdown_pct"), "strict_oracle_drawdown"),
                "enforce_drawdown_halt": False,
            }
            approved_symbols = tuple(base.observation_symbols)
            if event_boundary and "TLT" not in approved_symbols:
                # At an R46 marker the native engine deliberately exposes the
                # full account context; TLT is therefore approved for the
                # account overlay even when it is absent from the ordinary
                # V3 observation set.
                approved_symbols = (*approved_symbols, "TLT")
            risk_target = apply_account_risk(
                desired,
                replay_state=risk_state,
                approved_symbols=approved_symbols,
                maximum_allocation=oracle_maximum_allocation,
                caps=FROZEN_ACCOUNT_CAPS,
            )
            full_map = risk_target.weight_map
            applied_map = (
                {symbol: weight for symbol, weight in full_map.items() if symbol != "TLT"}
                if candidate and not event_boundary
                else dict(full_map)
            )
            oracle_target_maps[timestamp] = applied_map
            oracle_desired_target_maps[timestamp] = full_map
            if oracle_pending is not None:
                pending_equity = state_equity
                if candidate and not event_boundary:
                    tlt_position = state_holdings.get("TLT")
                    tlt_mark = state_marks.get(
                        "TLT", float(tlt_position.get("avg_price", 0.0)) if tlt_position else 0.0
                    )
                    tlt_value = (
                        float(tlt_position.get("qty", 0.0)) * tlt_mark if tlt_position else 0.0
                    )
                    overlay_cash_delta = 0.0
                    for raw_fill in _sequence(replay.get("fills", ()), "strict_oracle_pending_fills"):
                        raw_fill = _mapping(raw_fill, "strict_oracle_pending_fill")
                        if str(raw_fill.get("symbol", "")).upper() != "TLT":
                            continue
                        fill_timestamp = _timestamp(raw_fill.get("timestamp"), "strict_oracle_pending_timestamp")
                        if fill_timestamp > timestamp:
                            continue
                        qty = _number(raw_fill.get("qty"), "strict_oracle_pending_qty")
                        price = _number(raw_fill.get("price"), "strict_oracle_pending_price")
                        fee = _number(raw_fill.get("fee"), "strict_oracle_pending_fee")
                        if str(raw_fill.get("side", "")).upper() == "BUY":
                            overlay_cash_delta += qty * price + fee
                        elif str(raw_fill.get("side", "")).upper() == "SELL":
                            overlay_cash_delta -= qty * price - fee
                        else:
                            raise ValueError("strict_oracle_pending_side")
                    for raw_event in _sequence(inner.get("dividend_events", ()), "strict_oracle_pending_dividends"):
                        event = _mapping(raw_event, "strict_oracle_pending_dividend")
                        if str(event.get("symbol", "")).upper() == "TLT" and _timestamp(
                            event.get("timestamp"), "strict_oracle_pending_dividend_timestamp"
                        ) <= timestamp:
                            overlay_cash_delta -= _number(
                                event.get("credited_cash", 0.0), "strict_oracle_pending_dividend_cash"
                            )
                    pending_equity = state_equity + overlay_cash_delta - tlt_value
                order_ids = {
                    (symbol, side): (
                        f"{RESEARCH_ID if symbol == 'TLT' else _STRICT_INCUMBENT_NAME}|"
                        f"{_STRICT_CANDIDATE_VERSION if symbol == 'TLT' else _STRICT_INCUMBENT_VERSION}|"
                        f"{timestamp.isoformat()}|{symbol}|{side.lower()}|{rebalance_number}"
                    )
                    for symbol in set(applied_map) | set(state_holdings)
                    for side in ("BUY", "SELL")
                }
                oracle_pending.record_decision(
                    timestamp,
                    target_weights=applied_map,
                    pretrade_quantities={
                        str(symbol).upper(): float(position.get("qty", 0.0))
                        for symbol, position in state_holdings.items()
                    },
                    marks={str(symbol).upper(): float(mark) for symbol, mark in state_marks.items()},
                    equity=pending_equity,
                    order_id_by_request=order_ids,
                )
            return applied_map, full_map
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            raise ValueError("strict_v3_oracle_replay") from exc

    for index, (timestamp, raw_rebalance, raw_decision) in enumerate(
        zip(expected, rebalances, decisions, strict=True)
    ):
        rebalance_timestamp = _timestamp(
            raw_rebalance.get("decision_timestamp"), "strict_rebalance_timestamp"
        )
        decision_timestamp = _timestamp(raw_decision.get("decision_timestamp"), "strict_decision_timestamp")
        if rebalance_timestamp != timestamp or decision_timestamp != timestamp:
            raise ValueError("strict_rebalance_clock_timestamp")
        if raw_decision.get("signal_asof_timestamp") != timestamp.isoformat():
            raise ValueError("strict_decision_signal_timestamp")
        number_value = raw_rebalance.get("rebalance_number")
        if isinstance(number_value, bool) or not isinstance(number_value, int) or number_value != expected_numbers[index]:
            raise ValueError("strict_rebalance_number")
        decision_targets, owner_map = _strict_decision_target_map(raw_decision, field="strict_decision")
        if "desired_target_weights" not in raw_decision:
            raise ValueError("strict_decision_desired_target_missing")
        desired_targets, _desired_owner_map = _strict_decision_target_map(
            raw_decision,
            field="strict_decision_desired",
        )
        if set(desired_targets) - set(decision_targets) - {"TLT"}:
            raise ValueError("strict_decision_desired_target_identity")
        for symbol in set(decision_targets) | set(desired_targets):
            if symbol == "TLT":
                continue
            if abs(desired_targets.get(symbol, 0.0) - decision_targets.get(symbol, 0.0)) > _EPS:
                raise ValueError("strict_decision_desired_target_binding")
        if "target_weights" in raw_rebalance:
            rebalance_targets = _strict_rebalance_target_map(raw_rebalance)
            if set(decision_targets) != set(rebalance_targets) or any(
                abs(decision_targets[symbol] - rebalance_targets[symbol]) > _EPS for symbol in decision_targets
            ):
                raise ValueError("strict_decision_rebalance_target_binding")
        if abs(
            _number(raw_decision.get("target_cash_weight"), "strict_decision_cash_weight")
            - _number(raw_rebalance.get("target_cash_weight"), "strict_rebalance_cash_weight")
        ) > _EPS:
            raise ValueError("strict_decision_rebalance_cash_binding")
        target_cash_weight = _number(raw_decision.get("target_cash_weight"), "strict_decision_cash_weight")
        expected_cash_weight = max(0.0, 1.0 - sum(decision_targets.values()))
        if not 0.0 <= target_cash_weight <= 1.0 or abs(target_cash_weight - expected_cash_weight) > _EPS:
            raise ValueError("strict_decision_cash_weight")
        desired_cash_weight = _number(
            raw_decision.get("desired_target_cash_weight"),
            "strict_decision_desired_cash_weight",
        )
        expected_desired_cash_weight = max(0.0, 1.0 - sum(desired_targets.values()))
        if not 0.0 <= desired_cash_weight <= 1.0 or abs(desired_cash_weight - expected_desired_cash_weight) > _EPS:
            raise ValueError("strict_decision_desired_cash_weight")
        if raw_decision.get("suppression_reason") is not None:
            raise ValueError("strict_decision_suppression")

        if oracle_session is not None:
            expected_oracle_targets, expected_oracle_desired = oracle_expected(
                timestamp,
                event_boundary=candidate and timestamp in event_boundaries,
                rebalance_number=expected_numbers[index],
            )
            if set(decision_targets) != set(expected_oracle_targets) or any(
                abs(decision_targets[symbol] - expected_oracle_targets[symbol]) > _EPS
                for symbol in expected_oracle_targets
            ):
                raise ValueError("strict_v3_oracle_target_binding")
            if set(desired_targets) != set(expected_oracle_desired) or any(
                abs(desired_targets[symbol] - expected_oracle_desired[symbol]) > _EPS
                for symbol in expected_oracle_desired
            ):
                raise ValueError("strict_v3_oracle_desired_target_binding")
            expected_oracle_cash = max(0.0, 1.0 - sum(expected_oracle_targets.values()))
            expected_oracle_desired_cash = max(0.0, 1.0 - sum(expected_oracle_desired.values()))
            if abs(target_cash_weight - expected_oracle_cash) > _EPS or abs(
                desired_cash_weight - expected_oracle_desired_cash
            ) > _EPS:
                raise ValueError("strict_v3_oracle_cash_binding")
        if (
            abs(
                _number(raw_decision.get("unfilled_notional"), "strict_decision_unfilled_notional")
                - _number(raw_rebalance.get("unfilled_notional"), "strict_rebalance_unfilled_notional")
            ) > _EPS
            or abs(
                _number(raw_decision.get("rejected_notional"), "strict_decision_rejected_notional")
                - _number(raw_rebalance.get("rejected_notional"), "strict_rebalance_rejected_notional")
            ) > _EPS
        ):
            raise ValueError("strict_decision_rebalance_accounting")
        ownership = _mapping(raw_decision.get("ownership_attribution"), "strict_decision_ownership")
        expected_ownership = {
            "r46": ["TLT"] if "TLT" in decision_targets else [],
            "incumbent_v3": [symbol for symbol in decision_targets if symbol != "TLT"],
        }
        if dict(ownership) != expected_ownership:
            raise ValueError("strict_decision_ownership")
        if candidate:
            if "TLT" in decision_targets and abs(decision_targets["TLT"] - _STRICT_CANDIDATE_WEIGHT) > _EPS:
                raise ValueError("strict_candidate_tlt_weight")
            if "TLT" in decision_targets and owner_map.get("TLT") != "r46":
                raise ValueError("strict_candidate_tlt_owner")
            if timestamp not in event_boundaries and "TLT" in decision_targets:
                raise ValueError("strict_candidate_tlt_non_event_target")
            if "TLT" in desired_targets and abs(desired_targets["TLT"] - _STRICT_CANDIDATE_WEIGHT) > _EPS:
                raise ValueError("strict_candidate_desired_tlt_weight")
        elif "TLT" in decision_targets:
            raise ValueError("strict_control_tlt_target")
        elif "TLT" in desired_targets:
            raise ValueError("strict_control_desired_tlt_target")
        decision_by_timestamp[timestamp] = raw_decision
        rebalance_by_timestamp[timestamp] = raw_rebalance
        target_maps[timestamp] = decision_targets
        desired_target_maps[timestamp] = desired_targets
    # Every native order, including ordinary incumbent fills, is generated by
    # IdempotencyKeyFactory.  Bind the serialized identity to the trusted
    # decision clock instead of treating client_order_id as an opaque label.
    for raw_fill in _sequence(inner.get("fills"), "strict_decision_fills"):
        fill = _mapping(raw_fill, "strict_decision_fill")
        symbol = str(fill.get("symbol", ""))
        side = str(fill.get("side", "")).lower()
        decision_timestamp = _timestamp(fill.get("decision_timestamp"), "strict_decision_fill_timestamp")
        rebalance = rebalance_by_timestamp.get(decision_timestamp)
        if rebalance is None or side not in {"buy", "sell"}:
            raise ValueError("strict_decision_fill_clock")
        owner = str(fill.get("owner", ""))
        is_tlt_candidate = candidate and symbol == "TLT"
        expected_owner = "r46" if is_tlt_candidate else "incumbent_v3"
        if owner != expected_owner:
            raise ValueError("strict_decision_fill_owner")
        strategy_name = RESEARCH_ID if is_tlt_candidate else _STRICT_INCUMBENT_NAME
        strategy_version = _STRICT_CANDIDATE_VERSION if is_tlt_candidate else _STRICT_INCUMBENT_VERSION
        expected_order_id = (
            f"{strategy_name}|{strategy_version}|{decision_timestamp.isoformat()}|{symbol}|{side}|"
            f"{rebalance.get('rebalance_number')}"
        )
        if fill.get("client_order_id") != expected_order_id:
            raise ValueError("strict_decision_fill_order_id")
    trusted_metrics_raw = replay.get("trusted_metrics")
    if not isinstance(trusted_metrics_raw, dict):
        raise ValueError("strict_trusted_replay_metrics")
    trusted_metrics: dict[str, object] = trusted_metrics_raw
    trusted_metrics["rebalance_count"] = len(expected)
    return {
        "expected_timestamps": expected,
        "decision_by_timestamp": decision_by_timestamp,
        "rebalance_by_timestamp": rebalance_by_timestamp,
        "target_maps": target_maps,
        "desired_target_maps": desired_target_maps,
        "event_boundaries": event_boundaries,
        "oracle_target_maps": oracle_target_maps,
        "oracle_desired_target_maps": oracle_desired_target_maps,
        "oracle_ordered_bars": oracle_ordered_bars,
        "oracle_accepted_event_ids": tuple(sorted(oracle_accepted_ids)),
    }


def _strict_validate_event_contract(
    *,
    inner: Mapping[str, Any],
    replay: dict[str, Any],
    package_inputs: Any,
    bars: Mapping[tuple[str, datetime], Any],
    marker_timestamps: set[datetime],
    decision_contract: Mapping[str, Any],
    candidate: bool,
    turnover_band: float = 0.005,
    minimum_order_notional: float = 10.0,
    oracle_contract: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    schedules = list(getattr(package_inputs, "schedules", ()))
    oracle_ordered_bars = (
        oracle_contract.get("oracle_ordered_bars")
        if oracle_contract is not None
        else None
    )
    if not candidate:
        if _sequence(inner.get("event_records"), "strict_control_event_records"):
            raise ValueError("strict_control_event_records")
        if _sequence(inner.get("event_fill_statuses"), "strict_control_event_fill_statuses"):
            raise ValueError("strict_control_event_fill_statuses")
        if any(str(_mapping(raw, "strict_control_fill").get("symbol", "")) == "TLT" for raw in _sequence(inner.get("fills"), "strict_control_fills")):
            raise ValueError("strict_control_tlt_fill")
        for target_map in cast(Mapping[datetime, dict[str, float]], decision_contract["target_maps"]).values():
            if "TLT" in target_map:
                raise ValueError("strict_control_tlt_target")
        for timestamp, desired_map in cast(
            Mapping[datetime, dict[str, float]], decision_contract["desired_target_maps"]
        ).items():
            if "TLT" in desired_map:
                raise ValueError("strict_control_desired_tlt_target")
            applied_map = cast(Mapping[datetime, dict[str, float]], decision_contract["target_maps"])[timestamp]
            if desired_map != applied_map:
                raise ValueError("strict_control_desired_target_binding")
        replay["event_replay"] = {
            "accepted_event_ids": (),
            "event_episode_count": 0,
            "missing_event_count": 0,
            "capacity_suppression_count": 0,
        }
        return cast(dict[str, object], replay["event_replay"])

    schedule_by_id = {str(schedule.event_id): schedule for schedule in schedules}
    if len(schedule_by_id) != len(schedules) or len(schedules) != 127:
        raise ValueError("strict_event_schedule_completeness")
    records = [_mapping(raw, "strict_event_record") for raw in _sequence(inner.get("event_records"), "strict_event_records")]
    entry_records: dict[str, Mapping[str, Any]] = {}
    accepted_ids: set[str] = set()
    suppressed_ids: set[str] = set()
    for record in records:
        event_id = str(record.get("event_id", ""))
        schedule = schedule_by_id.get(event_id)
        if schedule is None:
            raise ValueError("strict_event_record_unknown")
        status = record.get("status")
        if status == "EXIT_DECISION":
            continue
        if event_id in entry_records or status not in {"ACCEPTED", "SUPPRESSED"}:
            raise ValueError("strict_event_record_duplicate")
        expected_entry_keys = {
            "event_id",
            "decision_timestamp",
            "entry_open",
            "exit_open",
            "status",
            "suppression_reason",
            "candidate_weight",
        }
        if set(record) != expected_entry_keys:
            raise ValueError("strict_event_record_fields")
        if (
            record.get("decision_timestamp") != schedule.entry_decision.isoformat()
            or record.get("entry_open") != schedule.entry_open.isoformat()
            or record.get("exit_open") != schedule.exit_open.isoformat()
        ):
            raise ValueError("strict_event_record_schedule_binding")
        if status == "ACCEPTED":
            accepted_ids.add(event_id)
            if record.get("suppression_reason") is not None or abs(_number(record.get("candidate_weight"), "strict_event_candidate_weight") - _STRICT_CANDIDATE_WEIGHT) > _EPS:
                raise ValueError("strict_event_record_accepted_weight")
        else:
            suppressed_ids.add(event_id)
            if record.get("suppression_reason") not in {
                "overlapping_event_no_pyramid",
                "total_positions_max",
                "combined_gross_max",
                "combined_cash_min",
            }:
                raise ValueError("strict_event_record_suppression_reason")
            if abs(_number(record.get("candidate_weight"), "strict_event_candidate_weight")) > _EPS:
                raise ValueError("strict_event_record_suppressed_weight")
        entry_records[event_id] = record
    if len(entry_records) != len(schedules) or accepted_ids & suppressed_ids:
        raise ValueError("strict_event_record_completeness")
    if oracle_contract is not None:
        expected_accepted = set(oracle_contract.get("oracle_accepted_event_ids", ()))
        if accepted_ids != expected_accepted:
            raise ValueError("strict_event_oracle_acceptance")
    exit_records: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if record.get("status") != "EXIT_DECISION":
            continue
        event_id = str(record.get("event_id", ""))
        schedule = schedule_by_id.get(event_id)
        if schedule is None or event_id not in accepted_ids or event_id in exit_records:
            raise ValueError("strict_event_exit_identity")
        expected_exit_keys = {
            "event_id",
            "decision_timestamp",
            "exit_open",
            "status",
            "suppression_reason",
            "candidate_weight",
        }
        if set(record) != expected_exit_keys or record.get("decision_timestamp") != schedule.exit_decision.isoformat() or record.get("exit_open") != schedule.exit_open.isoformat() or record.get("suppression_reason") is not None or abs(_number(record.get("candidate_weight"), "strict_event_candidate_weight")) > _EPS:
            raise ValueError("strict_event_exit_binding")
        exit_records[event_id] = record
    if set(exit_records) != accepted_ids:
        raise ValueError("strict_event_exit_completeness")

    # Strategy iteration records all entries before exits at a tied timestamp.
    expected_legs = [
        (schedule.entry_decision, 0, index, schedule.event_id, "entry") for index, schedule in enumerate(schedules)
    ]
    expected_legs.extend(
        (schedule.exit_decision, 1, index, schedule.event_id, "exit")
        for index, schedule in enumerate(schedules)
        if schedule.event_id in accepted_ids
    )
    expected_legs.sort(key=lambda item: (item[0], item[1], item[2]))
    if len(records) != len(expected_legs):
        raise ValueError("strict_event_record_order")
    for record, (_timestamp_value, leg_kind, _index, event_id, _kind_name) in zip(records, expected_legs, strict=True):
        if str(record.get("event_id")) != event_id or (leg_kind == 1) != (record.get("status") == "EXIT_DECISION"):
            raise ValueError("strict_event_record_order")

    target_maps = cast(Mapping[datetime, dict[str, float]], decision_contract["target_maps"])
    desired_target_maps = cast(Mapping[datetime, dict[str, float]], decision_contract["desired_target_maps"])
    decision_by_timestamp = cast(Mapping[datetime, Mapping[str, Any]], decision_contract["decision_by_timestamp"])
    expected_timestamps = cast(Sequence[datetime], decision_contract["expected_timestamps"])
    event_boundaries = cast(set[datetime], decision_contract["event_boundaries"])
    for timestamp in expected_timestamps:
        desired_map = desired_target_maps[timestamp]
        applied_map = target_maps[timestamp]
        active = any(
            event_id in accepted_ids
            and schedule.entry_decision <= timestamp < schedule.exit_decision
            for event_id, schedule in schedule_by_id.items()
        )
        expected_desired_tlt = _STRICT_CANDIDATE_WEIGHT if active else 0.0
        if abs(desired_map.get("TLT", 0.0) - expected_desired_tlt) > _EPS:
            raise ValueError("strict_event_desired_target")
        if set(desired_map) - set(applied_map) - {"TLT"}:
            raise ValueError("strict_event_desired_target_identity")
        for symbol in set(desired_map) | set(applied_map):
            if symbol != "TLT" and abs(desired_map.get(symbol, 0.0) - applied_map.get(symbol, 0.0)) > _EPS:
                raise ValueError("strict_event_desired_target_binding")
        expected_applied_tlt = expected_desired_tlt if timestamp in event_boundaries else 0.0
        if abs(applied_map.get("TLT", 0.0) - expected_applied_tlt) > _EPS:
            raise ValueError("strict_event_applied_target")
    for event_id, schedule in schedule_by_id.items():
        decision = decision_by_timestamp.get(schedule.entry_decision)
        if decision is None:
            raise ValueError("strict_event_entry_decision_missing")
        target_map = target_maps[schedule.entry_decision]
        target_weight = target_map.get("TLT", 0.0)
        if event_id in accepted_ids:
            if abs(target_weight - _STRICT_CANDIDATE_WEIGHT) > _EPS:
                raise ValueError("strict_event_accepted_target")
            prior_active = any(
                other_id in accepted_ids
                and other_id != event_id
                and other.entry_decision <= schedule.entry_decision < other.exit_decision
                for other_id, other in schedule_by_id.items()
            )
            if prior_active:
                raise ValueError("strict_event_overlap_acceptance")
        else:
            record = entry_records[event_id]
            prior_active = any(
                other_id in accepted_ids and other.entry_decision <= schedule.entry_decision < other.exit_decision
                for other_id, other in schedule_by_id.items()
            )
            if prior_active:
                # A suppressed overlapping entry does not remove the already
                # active sleeve.  The decision target therefore remains .025;
                # only this event's episode row is suppressed.
                if abs(target_weight - _STRICT_CANDIDATE_WEIGHT) > _EPS:
                    raise ValueError("strict_event_suppressed_active_target")
                if record.get("suppression_reason") != "overlapping_event_no_pyramid":
                    raise ValueError("strict_event_overlap_suppression")
            else:
                if abs(target_weight) > _EPS:
                    raise ValueError("strict_event_suppressed_target")
                _cash, holdings, _marks, _equity, _tlt_reference = _strict_replay_state_at(
                    inner=inner,
                    replay=replay,
                    bars=bars,
                    timestamp=schedule.entry_decision,
                    marker_timestamps=marker_timestamps,
                    ordered_bars=cast(Sequence[Any] | None, oracle_ordered_bars),
                )
                base_map = {symbol: weight for symbol, weight in target_map.items() if symbol != "TLT" and weight > _EPS}
                projected = set(holdings) | set(base_map) | {"TLT"}
                base_invested = sum(base_map.values())
                base_cash = _number(decision.get("target_cash_weight"), "strict_event_base_cash")
                if len(projected) > int(FROZEN_CAPS["total_positions_max"]):
                    expected_reason = "total_positions_max"
                elif base_invested + _STRICT_CANDIDATE_WEIGHT > float(FROZEN_CAPS["combined_gross_max"]) + _EPS:
                    expected_reason = "combined_gross_max"
                elif base_cash - _STRICT_CANDIDATE_WEIGHT < float(FROZEN_CAPS["combined_cash_min"]) - _EPS:
                    expected_reason = "combined_cash_min"
                else:
                    raise ValueError("strict_event_suppression_unproven")
                if record.get("suppression_reason") != expected_reason:
                    raise ValueError("strict_event_suppression_reason")
        exit_decision = decision_by_timestamp.get(schedule.exit_decision)
        if exit_decision is None:
            raise ValueError("strict_event_exit_decision_missing")
        if abs(target_maps[schedule.exit_decision].get("TLT", 0.0)) > _EPS:
            raise ValueError("strict_event_exit_target")

    fills = [_mapping(raw, "strict_event_fill") for raw in _sequence(inner.get("fills"), "strict_event_fills")]
    leg_lookup: dict[tuple[datetime, str], tuple[str, datetime, int]] = {}
    rebalance_by_timestamp = cast(Mapping[datetime, Mapping[str, Any]], decision_contract["rebalance_by_timestamp"])
    for event_id in accepted_ids:
        schedule = schedule_by_id[event_id]
        for timestamp, side, decision_timestamp in (
            (schedule.entry_open, "BUY", schedule.entry_decision),
            (schedule.exit_open, "SELL", schedule.exit_decision),
        ):
            if (timestamp, side) in leg_lookup:
                raise ValueError("strict_event_leg_ambiguous")
            rebalance = rebalance_by_timestamp.get(decision_timestamp)
            if rebalance is None:
                raise ValueError("strict_event_order_decision_missing")
            rebalance_number = rebalance.get("rebalance_number")
            if isinstance(rebalance_number, bool) or not isinstance(rebalance_number, int):
                raise ValueError("strict_event_order_rebalance_number")
            leg_lookup[(timestamp, side)] = (event_id, decision_timestamp, rebalance_number)
    leg_quantities: dict[tuple[str, str], float] = {}
    for fill in fills:
        if str(fill.get("symbol", "")) != "TLT":
            continue
        fill_timestamp = _timestamp(fill.get("timestamp"), "strict_event_fill_timestamp")
        side = str(fill.get("side", "")).upper()
        leg = leg_lookup.get((fill_timestamp, side))
        if leg is None:
            raise ValueError("strict_event_fill_unknown_leg")
        event_id, decision_timestamp, rebalance_number = leg
        if (
            fill.get("owner") != "r46"
            or _timestamp(fill.get("decision_timestamp"), "strict_event_fill_decision") != decision_timestamp
        ):
            raise ValueError("strict_event_fill_leg_binding")
        expected_order_id = (
            f"{RESEARCH_ID}|{_STRICT_CANDIDATE_VERSION}|{decision_timestamp.isoformat()}|TLT|"
            f"{side.lower()}|{rebalance_number}"
        )
        if fill.get("client_order_id") != expected_order_id:
            raise ValueError("strict_event_fill_order_id")
        key = (event_id, side)
        leg_quantities[key] = leg_quantities.get(key, 0.0) + _number(fill.get("qty"), "strict_event_fill_qty")
    statuses = [_mapping(raw, "strict_event_fill_status") for raw in _sequence(inner.get("event_fill_statuses"), "strict_event_fill_statuses")]
    status_by_id: dict[str, Mapping[str, Any]] = {}
    for status in statuses:
        event_id = str(status.get("event_id", ""))
        if event_id not in accepted_ids or event_id in status_by_id:
            raise ValueError("strict_event_fill_status_identity")
        expected_status_keys = {
            "event_id",
            "entry_open",
            "exit_open",
            "requested_qty",
            "entry_target_qty",
            "entry_current_qty",
            "entry_order_required",
            "exit_target_qty",
            "exit_current_qty",
            "exit_requested_qty",
            "exit_order_required",
            "entry_filled_qty",
            "exit_filled_qty",
            "entry_status",
            "exit_status",
            "eligible_episode",
        }
        if set(status) != expected_status_keys:
            raise ValueError("strict_event_fill_status_fields")
        schedule = schedule_by_id[event_id]
        if status.get("entry_open") != schedule.entry_open.isoformat() or status.get("exit_open") != schedule.exit_open.isoformat():
            raise ValueError("strict_event_fill_status_schedule")
        status_by_id[event_id] = status
    if set(status_by_id) != accepted_ids:
        raise ValueError("strict_event_fill_status_completeness")

    trusted_event_rows: list[dict[str, object]] = []
    for schedule in schedules:
        event_id = schedule.event_id
        if event_id not in accepted_ids:
            continue
        _cash, _holdings, _marks, decision_equity, tlt_reference = _strict_replay_state_at(
            inner=inner,
            replay=replay,
            bars=bars,
            timestamp=schedule.entry_decision,
            marker_timestamps=marker_timestamps,
            ordered_bars=cast(Sequence[Any] | None, oracle_ordered_bars),
        )
        if tlt_reference is None or tlt_reference <= 0:
            raise ValueError("strict_event_reference_missing")
        entry_current_qty = float(_holdings.get("TLT", {}).get("qty", 0.0)) if _holdings else 0.0
        entry_target_qty = _STRICT_CANDIDATE_WEIGHT * decision_equity / tlt_reference
        entry_current_weight = (
            entry_current_qty * tlt_reference / decision_equity if decision_equity > 0 else 0.0
        )
        entry_delta = entry_target_qty - entry_current_qty
        entry_order_required = (
            abs(_STRICT_CANDIDATE_WEIGHT - entry_current_weight) >= turnover_band
            and abs(entry_delta) * tlt_reference >= minimum_order_notional
        )
        requested_qty = abs(entry_delta) if entry_order_required else 0.0
        _exit_cash, exit_holdings, _exit_marks, exit_equity, exit_reference = _strict_replay_state_at(
            inner=inner,
            replay=replay,
            bars=bars,
            timestamp=schedule.exit_decision,
            marker_timestamps=marker_timestamps,
            ordered_bars=cast(Sequence[Any] | None, oracle_ordered_bars),
        )
        if exit_reference is None or exit_reference <= 0:
            raise ValueError("strict_event_exit_reference_missing")
        exit_current_qty = float(exit_holdings.get("TLT", {}).get("qty", 0.0))
        exit_current_weight = exit_current_qty * exit_reference / exit_equity if exit_equity > 0 else 0.0
        exit_order_required = (
            exit_current_weight >= turnover_band
            and abs(exit_current_qty) * exit_reference >= minimum_order_notional
        )
        exit_requested_qty = abs(exit_current_qty) if exit_order_required else 0.0
        entry_qty = leg_quantities.get((event_id, "BUY"), 0.0)
        exit_qty = leg_quantities.get((event_id, "SELL"), 0.0)
        if not entry_order_required and entry_qty > _EPS:
            raise ValueError("strict_event_unexpected_entry_fill")
        if not exit_order_required and exit_qty > _EPS:
            raise ValueError("strict_event_unexpected_exit_fill")
        if entry_qty > requested_qty + _EPS:
            raise ValueError("strict_event_entry_overfill")
        if exit_qty > exit_requested_qty + _EPS:
            raise ValueError("strict_event_exit_overfill")
        entry_status = "UNFILLED" if entry_qty <= _EPS else "FULL" if entry_qty + _EPS >= requested_qty > 0 else "PARTIAL"
        exit_status = (
            "UNFILLED"
            if exit_qty <= _EPS
            else "FULL"
            if exit_requested_qty > 0 and exit_qty + _EPS >= exit_requested_qty
            else "PARTIAL"
        )
        status = status_by_id[event_id]
        if abs(_number(status.get("requested_qty"), "strict_event_requested_qty") - requested_qty) > _EPS:
            raise ValueError("strict_event_requested_qty")
        if (
            abs(_number(status.get("entry_filled_qty"), "strict_event_entry_filled_qty") - entry_qty) > _EPS
            or abs(_number(status.get("exit_filled_qty"), "strict_event_exit_filled_qty") - exit_qty) > _EPS
            or abs(_number(status.get("entry_target_qty"), "strict_event_entry_target_qty") - entry_target_qty) > _EPS
            or abs(_number(status.get("entry_current_qty"), "strict_event_entry_current_qty") - entry_current_qty) > _EPS
            or status.get("entry_order_required") is not entry_order_required
            or abs(_number(status.get("exit_target_qty"), "strict_event_exit_target_qty")) > _EPS
            or abs(_number(status.get("exit_current_qty"), "strict_event_exit_current_qty") - exit_current_qty) > _EPS
            or abs(_number(status.get("exit_requested_qty"), "strict_event_exit_requested_qty") - exit_requested_qty) > _EPS
            or status.get("exit_order_required") is not exit_order_required
            or status.get("entry_status") != entry_status
            or status.get("exit_status") != exit_status
            or status.get("eligible_episode") is not (entry_status == "FULL" and exit_status == "FULL")
        ):
            raise ValueError("strict_event_fill_status")
        trusted_event_rows.append(
            {
                "event_id": event_id,
                "entry_status": entry_status,
                "exit_status": exit_status,
                "eligible_episode": entry_status == "FULL" and exit_status == "FULL",
                "requested_qty": requested_qty,
                "entry_target_qty": entry_target_qty,
                "entry_current_qty": entry_current_qty,
                "entry_order_required": entry_order_required,
                "exit_target_qty": 0.0,
                "exit_current_qty": exit_current_qty,
                "exit_requested_qty": exit_requested_qty,
                "exit_order_required": exit_order_required,
                "entry_filled_qty": entry_qty,
                "exit_filled_qty": exit_qty,
            }
        )
    event_replay = {
        "accepted_event_ids": tuple(schedule.event_id for schedule in schedules if schedule.event_id in accepted_ids),
        "event_episode_count": len(accepted_ids),
        "missing_event_count": sum(row["entry_status"] != "FULL" or row["exit_status"] != "FULL" for row in trusted_event_rows),
        "capacity_suppression_count": sum(
            entry_records[event_id].get("suppression_reason") == "total_positions_max"
            for event_id in suppressed_ids
        ),
        "event_fill_statuses": tuple(trusted_event_rows),
        "event_records": tuple(dict(record) for record in records),
    }
    replay["event_replay"] = event_replay
    return event_replay


def _strict_verify_ledger_sources(
    *,
    package_inputs: Any,
    incumbent: Any,
    bars: Mapping[tuple[str, datetime], Any],
    ledger: Mapping[str, Any],
    benchmark_bars: Sequence[Any] | None = None,
    expected_curve_timestamps: Sequence[datetime] | None = None,
) -> dict[str, dict[str, object]]:
    try:
        from scripts.research import r46_treasury_auction_unwind_tlt_v2_2026_09_10 as runner

        config = runner._load_r46_config(None, repo_root=incumbent.source_paths.get("repo_root", REPO), test_only=False)
        _trusted_universe, trusted_universe_version, trusted_universe_status = runner._build_r46_universe(
            incumbent,
            config=config,
            test_only=False,
        )
    except Exception as exc:
        raise ValueError("strict_config_source") from exc
    execution_config = getattr(config, "execution", None)
    trusted_turnover_band = _number(
        getattr(execution_config, "rebalance_band_pct", 0.005),
        "strict_turnover_band",
    )
    trusted_minimum_order_notional = _number(
        getattr(execution_config, "minimum_order_notional", 10.0),
        "strict_minimum_order_notional",
    )
    try:
        maximum_allocation = _number(
            getattr(getattr(getattr(config, "strategy"), "equity"), "maximum_allocation", 0.55),
            "strict_maximum_allocation",
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("strict_v3_oracle_config") from exc
    # ``_strict_source_dataset`` inserts executable bars in the trusted
    # timestamp/symbol order.  Preserve that order here: sorting the complete
    # production bar table again is both redundant and needlessly expensive.
    ordered_bars = tuple(bars.values())
    oracle_groups: dict[str, list[Any]] = {}
    oracle_timestamp_groups: dict[str, list[datetime]] = {}
    for bar in ordered_bars:
        symbol = str(bar.symbol).upper()
        oracle_groups.setdefault(symbol, []).append(bar)
        oracle_timestamp_groups.setdefault(symbol, []).append(bar.timestamp)
    oracle_groups_frozen = {symbol: tuple(series) for symbol, series in oracle_groups.items()}
    oracle_timestamp_groups_frozen = {
        symbol: tuple(timestamps) for symbol, timestamps in oracle_timestamp_groups.items()
    }
    oracle_target_cache: dict[datetime, Any] = {}
    dividend_rows_by_key: dict[str, tuple[datetime, float, datetime, str, str, str]] = {}
    try:
        artifact_spec = _mapping(package_inputs.package.get("artifacts"), "strict_package_artifacts").get("dividends")
        dividend_path = _strict_contained_file(package_inputs.root, _mapping(artifact_spec, "strict_dividend_artifact").get("path"), "dividends", relative_only=True)
        calendar_opens = {
            str(row.get("regular_session_date")): _timestamp(row.get("regular_open_timestamp"), "strict_calendar_open")
            for row in getattr(package_inputs, "calendar", ())
            if isinstance(row, Mapping)
        }
        with dividend_path.open(newline="", encoding="utf-8") as handle:
            for raw_row in csv.DictReader(handle):
                key = str(raw_row.get("duplicate_event_key", ""))
                if not key or key in dividend_rows_by_key:
                    raise ValueError("strict_dividend_source_duplicate")
                if raw_row.get("source_row_sha256") not in (None, ""):
                    raise ValueError("strict_dividend_source_row_hash_attacker_supplied")
                if raw_row.get("action_type") != "cash_dividend" or raw_row.get("currency") != "USD":
                    raise ValueError("strict_dividend_source_dimensions")
                event_timestamp = _timestamp(raw_row.get("event_timestamp"), "strict_source_dividend_timestamp")
                amount = _number(raw_row.get("cash_or_ratio"), "strict_source_dividend_amount")
                available_at = _timestamp(raw_row.get("available_at"), "strict_source_dividend_available_at")
                if available_at > event_timestamp:
                    raise ValueError("strict_dividend_source_availability")
                effective_date = str(raw_row.get("effective_date", ""))
                expected_open = calendar_opens.get(effective_date)
                if expected_open is not None and (event_timestamp != expected_open or available_at != expected_open):
                    raise ValueError("strict_dividend_source_calendar")
                source_hash = str(raw_row.get("source_document_hash", ""))
                canonical_row = {key_name: value for key_name, value in raw_row.items() if key_name != "source_row_sha256"}
                row_hash = hashlib.sha256(_canonical_bytes(canonical_row)).hexdigest()
                if not _valid_hex64(row_hash) or key in dividend_rows_by_key:
                    raise ValueError("strict_dividend_source_row_hash")
                dividend_rows_by_key[key] = (event_timestamp, amount, available_at, source_hash, row_hash, "TLT")
        # V5 corporate-action rows have a different schema and no package
        # duplicate_event_key.  Bind them by their immutable raw_id plus
        # ex-date, while retaining the canonical row hash for replay.
        v5_root = Path(str(incumbent.source_paths.get("repo_root", REPO))).resolve()
        v5_path_value = incumbent.source_paths.get("corporate_actions")
        v5_path = _strict_contained_file(v5_root, v5_path_value, "v5_corporate_actions")
        with v5_path.open(newline="", encoding="utf-8") as handle:
            for raw_row in csv.DictReader(handle):
                if raw_row.get("action_type") != "cash_dividends" or not raw_row.get("ex_date"):
                    continue
                amount = _number(
                    raw_row.get("amount") or raw_row.get("rate") or raw_row.get("cash_amount"),
                    "strict_v5_dividend_amount",
                )
                if amount <= 0:
                    continue
                symbol = str(raw_row.get("symbol", ""))
                timestamp = _timestamp(f"{raw_row['ex_date']}T00:00:00+00:00", "strict_v5_dividend_timestamp")
                key = f"v5:{str(raw_row.get('raw_id', '')).strip() or symbol}:{raw_row['ex_date']}"
                row_hash = hashlib.sha256(_canonical_bytes(raw_row)).hexdigest()
                if key in dividend_rows_by_key:
                    raise ValueError("strict_dividend_source_duplicate")
                dividend_rows_by_key[key] = (timestamp, amount, timestamp, "", row_hash, symbol)
    except (OSError, TypeError, ValueError, csv.Error) as exc:
        raise ValueError("strict_dividend_source_load") from exc
    cells = _sequence(ledger.get("cells"), "strict_ledger_cells")
    trusted_event_replays: dict[str, dict[str, object]] = {}
    for raw_cell in cells:
        cell = _mapping(raw_cell, "strict_ledger_cell")
        multiplier = _number(cell.get("cost_multiplier"), "strict_cell_multiplier")
        expected_contract = runner._cost_contract(config, multiplier)
        for arm in ("candidate", "control"):
            inner = _mapping(cell.get(arm), f"strict_{arm}_ledger")
            # Every persisted economic-shaped cell, including the shifted
            # Phase-C timing ledgers, carries a producer decision contract and
            # must bind its universe to the trusted source.  The timing cell's
            # shifted package identity is additionally checked by the strict
            # placebo wrapper below.
            if "decisions" in inner and (
                inner.get("universe_version") != trusted_universe_version
                or inner.get("universe_status") != trusted_universe_status
            ):
                raise ValueError("strict_universe_source_binding")
            contract = _mapping(inner.get("cost_contract"), "strict_cost_contract")
            if dict(contract) != dict(expected_contract):
                raise ValueError("strict_cost_contract_source_mismatch")
            expected_schedules = [
                schedule.to_dict() if hasattr(schedule, "to_dict") else dict(schedule)
                for schedule in getattr(package_inputs, "schedules", ())
            ]
            observed_schedules = _sequence(inner.get("event_schedules"), "strict_event_schedules")
            if list(observed_schedules) != expected_schedules:
                raise ValueError("strict_event_schedule_binding")
            expected_event_ids = {
                str(event.get("canonical_event_id", ""))
                if isinstance(event, Mapping)
                else str(getattr(event, "canonical_event_id", ""))
                for event in getattr(package_inputs, "events", ())
            }
            event_records = _sequence(inner.get("event_records"), "strict_event_records")
            for raw_record in event_records:
                record = _mapping(raw_record, "strict_event_record")
                if str(record.get("event_id", "")) not in expected_event_ids:
                    raise ValueError("strict_event_record_binding")
            marker_records = _sequence(inner.get("marker_records"), "strict_marker_records")
            expected_markers = {
                (marker.timestamp, str(marker.symbol))
                for marker in getattr(package_inputs, "markers", ())
            }
            observed_markers = {
                (_timestamp(_mapping(raw_marker, "strict_marker_record").get("timestamp"), "strict_marker_timestamp"), str(_mapping(raw_marker, "strict_marker_record").get("symbol", "")))
                for raw_marker in marker_records
            }
            if observed_markers != expected_markers:
                raise ValueError("strict_marker_binding")
            fills = _sequence(inner.get("fills"), "strict_fills")
            for raw_fill in fills:
                fill = _mapping(raw_fill, "strict_fill")
                timestamp = _timestamp(fill.get("timestamp"), "strict_fill_timestamp")
                symbol = str(fill.get("symbol", ""))
                bar = bars.get((symbol, timestamp))
                if bar is None:
                    raise ValueError("strict_fill_source_bar_missing")
                if fill.get("source_bar_key") != f"{symbol}|{timestamp.isoformat()}":
                    raise ValueError("strict_fill_source_key")
                if fill.get("source_bar_row_sha256") != _bar_source_row_hash(bar):
                    raise ValueError("strict_fill_source_row_hash")
                if abs(_number(fill.get("reference_price"), "strict_reference_price") - float(bar.open)) > _EPS:
                    raise ValueError("strict_fill_reference_price_source")
                if _number(fill.get("qty"), "strict_fill_qty") > float(bar.volume) * 0.10 + _EPS:
                    raise ValueError("strict_fill_participation")
            dividends = _sequence(inner.get("dividend_events"), "strict_dividends")
            seen_dividend_keys: set[str] = set()
            for raw_event in dividends:
                event = _mapping(raw_event, "strict_dividend")
                source_key = event.get("source_event_key")
                source_row_hash = event.get("source_row_sha256")
                if not isinstance(source_key, str) or not isinstance(source_row_hash, str) or source_key in seen_dividend_keys:
                    raise ValueError("strict_dividend_source_binding")
                seen_dividend_keys.add(source_key)
                source_row = dividend_rows_by_key.get(source_key)
                if source_row is None:
                    raise ValueError("strict_dividend_source_key")
                source_timestamp, source_amount, source_available_at, source_document_hash, source_row_hash, source_symbol = source_row
                event_timestamp = _timestamp(event.get("timestamp"), "strict_dividend_timestamp")
                if (
                    event_timestamp != source_timestamp
                    or abs(_number(event.get("per_share"), "strict_dividend_per_share") - source_amount) > _EPS
                    or event_timestamp < source_available_at
                    or source_row_hash != str(event.get("source_row_sha256"))
                    or (source_symbol and str(event.get("symbol")) != source_symbol)
                ):
                    raise ValueError("strict_dividend_source_mismatch")
            terminal = _mapping(inner.get("terminal_liquidation"), "strict_terminal")
            audit_rows = _sequence(inner.get("terminal_audit"), "strict_terminal_audit")
            if terminal.get("applied") and not audit_rows:
                raise ValueError("strict_terminal_audit_missing")
            seen_terminal: set[str] = set()
            terminal_fee_total = 0.0
            terminal_slippage_total = 0.0
            terminal_spread_total = 0.0
            terminal_proceeds_total = 0.0
            for raw_terminal_row in audit_rows:
                terminal_row: Mapping[str, Any] = _mapping(raw_terminal_row, "strict_terminal_row")
                symbol = str(terminal_row.get("symbol", ""))
                timestamp = _timestamp(terminal_row.get("last_bar"), "strict_terminal_bar")
                if symbol in seen_terminal:
                    raise ValueError("strict_terminal_identity")
                seen_terminal.add(symbol)
                bar = bars.get((symbol, timestamp))
                if bar is None or abs(_number(terminal_row.get("last_close"), "strict_terminal_close") - float(bar.close)) > _EPS:
                    raise ValueError("strict_terminal_source_close")
                qty = _number(terminal_row.get("qty"), "strict_terminal_qty")
                fill_price = _number(terminal_row.get("fill_price"), "strict_terminal_fill_price")
                policy = str(terminal_row.get("effective_policy"))
                if policy not in {"T1", "FINAL_BAR"}:
                    raise ValueError("strict_terminal_policy")
                terminal_map = getattr(incumbent, "terminal_map", {}) or {}
                terminal_info = terminal_map.get(symbol)
                if terminal_info is not None:
                    if policy != "T1" or timestamp != getattr(terminal_info, "last_bar", timestamp):
                        raise ValueError("strict_terminal_policy")
                    entity_id = str(getattr(terminal_info, "entity_id", symbol))
                else:
                    symbol_timestamps = [
                        source_bar.timestamp
                        for (source_symbol, _), source_bar in bars.items()
                        if source_symbol == symbol
                    ]
                    if not symbol_timestamps or timestamp != max(symbol_timestamps):
                        raise ValueError("strict_terminal_final_bar")
                    entity_id = symbol
                identity = f"{entity_id}|{timestamp.isoformat()}|{policy}"
                expected_map_hash = hashlib.sha256(
                    _canonical_bytes(
                        {
                            "entity_id": entity_id,
                            "last_bar": timestamp.isoformat(),
                            "effective_policy": policy,
                        }
                    )
                ).hexdigest()
                if terminal_row.get("terminal_map_identity") != identity or terminal_row.get("terminal_map_hash") != expected_map_hash:
                    raise ValueError("strict_terminal_map_identity")
                for field in ("avg_price", "entry_fee", "realized_pnl", "pre_equity", "post_equity"):
                    _number(terminal_row.get(field), f"strict_terminal_{field}")
                factor = 1.0
                expected_price, expected_slippage, expected_spread, expected_fee = _expected_fill_costs(
                    symbol=symbol,
                    side="SELL",
                    qty=qty,
                    price=fill_price,
                    reference_price=float(bar.close) * factor,
                    timestamp=timestamp,
                    contract=contract,
                )
                if abs(fill_price - expected_price) > _EPS or abs(_number(terminal_row.get("fee"), "strict_terminal_fee") - expected_fee) > _EPS:
                    raise ValueError("strict_terminal_cost_reconciliation")
                observed_proceeds = _number(terminal_row.get("proceeds"), "strict_terminal_proceeds")
                if abs(observed_proceeds - (qty * fill_price - expected_fee)) > _EPS:
                    raise ValueError("strict_terminal_proceeds_reconciliation")
                terminal_fee_total += expected_fee
                terminal_slippage_total += expected_slippage
                terminal_spread_total += expected_spread
                terminal_proceeds_total += observed_proceeds
                if terminal_row.get("position_removed") is not True:
                    raise ValueError("strict_terminal_position_not_removed")
            if terminal.get("applied"):
                if inner.get("positions") not in ({}, None):
                    raise ValueError("strict_terminal_positions_remaining")
                if audit_rows:
                    first = _mapping(audit_rows[0], "strict_terminal_row")
                    last = _mapping(audit_rows[-1], "strict_terminal_row")
                    if abs(_number(first.get("cash_before"), "strict_terminal_cash_before") - _number(terminal.get("cash_before"), "strict_terminal_cash_before")) > _EPS:
                        raise ValueError("strict_terminal_cash_before")
                    if abs(_number(last.get("cash_after"), "strict_terminal_cash_after") - _number(terminal.get("cash_after"), "strict_terminal_cash_after")) > _EPS:
                        raise ValueError("strict_terminal_cash_after")
                if abs(terminal_fee_total - _number(terminal.get("fee"), "strict_terminal_fee_total")) > _EPS:
                    raise ValueError("strict_terminal_fee_total")
                if abs(terminal_slippage_total - _number(terminal.get("slippage_cost"), "strict_terminal_slippage_total")) > _EPS:
                    raise ValueError("strict_terminal_slippage_total")
                if abs(terminal_spread_total - _number(terminal.get("spread_cost"), "strict_terminal_spread_total")) > _EPS:
                    raise ValueError("strict_terminal_spread_total")
                if abs(terminal_proceeds_total - _number(terminal.get("cash_delta"), "strict_terminal_cash_delta")) > _EPS:
                    raise ValueError("strict_terminal_cash_delta")
            elif audit_rows:
                raise ValueError("strict_terminal_audit_without_event")
            trusted_replay = _strict_replay_inner(
                inner=inner,
                contract=contract,
                bars=bars,
                dividend_rows_by_key=dividend_rows_by_key,
                incumbent=incumbent,
                expected_curve_timestamps=expected_curve_timestamps,
            )
            audit_context = _mapping(inner.get("audit_context"), "strict_audit_context")
            if "turnover_band" in audit_context and abs(
                _number(audit_context.get("turnover_band"), "strict_turnover_band") - trusted_turnover_band
            ) > _EPS:
                raise ValueError("strict_turnover_band_contract")
            if "minimum_order_notional" in audit_context and abs(
                _number(audit_context.get("minimum_order_notional"), "strict_minimum_order_notional")
                - trusted_minimum_order_notional
            ) > _EPS:
                raise ValueError("strict_minimum_order_notional_contract")
            marker_timestamps = {timestamp for timestamp, _symbol in expected_markers}
            event_boundaries = (
                {
                    boundary
                    for schedule in getattr(package_inputs, "schedules", ())
                    for boundary in (schedule.entry_decision, schedule.exit_decision)
                }
                if arm == "candidate"
                else set()
            )
            # The pre-existing timing diagnostic stores native ledgers before
            # the producer contract was added.  Main economic cells always
            # carry ``decisions`` (including an empty control list) and take
            # the strict clock/event path.  Timing ledgers remain outside this
            # Phase-B closure; their persisted identity replay is Phase C.
            if "decisions" in inner:
                decision_contract = _strict_validate_decision_clock(
                    inner=inner,
                    replay=trusted_replay,
                    bars=bars,
                    marker_timestamps=marker_timestamps,
                    event_boundaries=event_boundaries,
                    expected_curve_timestamps=expected_curve_timestamps,
                    candidate=arm == "candidate",
                    turnover_band=trusted_turnover_band,
                    minimum_order_notional=trusted_minimum_order_notional,
                    oracle_context={
                        "bars": bars,
                        "_groups": oracle_groups_frozen,
                        "_timestamp_groups": oracle_timestamp_groups_frozen,
                        "_target_cache": oracle_target_cache,
                        "universe": _trusted_universe,
                        "terminal_map": getattr(incumbent, "terminal_map", {}),
                        "schedules": getattr(package_inputs, "schedules", ()),
                        "maximum_allocation": maximum_allocation,
                        "ordered_bars": ordered_bars,
                    },
                )
                event_replay = _strict_validate_event_contract(
                    inner=inner,
                    replay=trusted_replay,
                    package_inputs=package_inputs,
                    bars=bars,
                    marker_timestamps=marker_timestamps,
                    decision_contract=decision_contract,
                    candidate=arm == "candidate",
                    turnover_band=trusted_turnover_band,
                    minimum_order_notional=trusted_minimum_order_notional,
                    oracle_contract=decision_contract,
                )
                _strict_validate_rebalance_details(
                    inner=inner,
                    replay=trusted_replay,
                    bars=bars,
                    marker_timestamps=marker_timestamps,
                    decision_contract=decision_contract,
                    candidate=arm == "candidate",
                    turnover_band=trusted_turnover_band,
                    minimum_order_notional=trusted_minimum_order_notional,
                    oracle_contract=decision_contract,
                )
                trusted_event_replays[
                    f"{multiplier:.12g}:{arm}"
                ] = dict(event_replay)
            _strict_recompute_curve(
                inner,
                expected_benchmark_bars=benchmark_bars,
                trusted_replay=trusted_replay,
            )
    return trusted_event_replays


def _strict_recompute_curve(
    inner: Mapping[str, Any],
    *,
    expected_benchmark_bars: Sequence[Any] | None = None,
    trusted_replay: Mapping[str, Any] | None = None,
) -> None:
    metrics = _mapping(inner.get("result_metrics"), "strict_result_metrics")
    curve_raw = _sequence(inner.get("equity_curve"), "strict_equity_curve")
    timestamps_raw = _sequence(inner.get("equity_curve_timestamps"), "strict_equity_curve_timestamps")
    if not curve_raw or len(curve_raw) != len(timestamps_raw):
        raise ValueError("strict_equity_curve_missing")
    curve = [_number(value, "strict_equity_value") for value in curve_raw]
    timestamps = [_timestamp(value, "strict_equity_timestamp") for value in timestamps_raw]
    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:], strict=False)):
        raise ValueError("strict_equity_timestamp_order")
    starting = _number(metrics.get("starting_equity"), "strict_starting_equity")
    ending = curve[-1]
    if abs(starting - 100_000.0) > _EPS or abs(_number(metrics.get("ending_equity"), "strict_ending_equity") - ending) > _EPS:
        raise ValueError("strict_equity_endpoint")
    total_return = ending / starting - 1.0
    if abs(_number(metrics.get("total_return"), "strict_total_return") - total_return) > _EPS:
        raise ValueError("strict_total_return")
    peak = starting
    max_drawdown = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    if abs(_number(metrics.get("max_drawdown"), "strict_max_drawdown") - max_drawdown) > _EPS:
        raise ValueError("strict_max_drawdown")
    duration_days = int((timestamps[-1] - timestamps[0]).total_seconds() / 86_400)
    if metrics.get("duration_days") != duration_days:
        raise ValueError("strict_duration_days")
    if abs(_number(metrics.get("net_return"), "strict_net_return") - total_return) > _EPS:
        raise ValueError("strict_net_return")
    benchmark = [_number(value, "strict_benchmark_value") for value in _sequence(inner.get("benchmark_curve"), "strict_benchmark_curve")]
    benchmark_timestamps = [
        _timestamp(value, "strict_benchmark_timestamp")
        for value in _sequence(inner.get("benchmark_curve_timestamps"), "strict_benchmark_curve_timestamps")
    ]
    # A benchmark is part of the economic observation, not an optional report
    # decoration.  Requiring the complete, timestamp-aligned curve prevents an
    # attacker from omitting it (or substituting a one-point/arbitrary curve)
    # while retaining a rehashed ledger.
    if not benchmark or len(benchmark) != len(curve) or len(benchmark_timestamps) != len(benchmark):
        raise ValueError("strict_benchmark_curve_missing")
    if benchmark_timestamps != timestamps or any(value <= 0 for value in benchmark):
        raise ValueError("strict_benchmark_curve_binding")
    if expected_benchmark_bars is not None:
        expected_benchmark = tuple(
            sorted(
                (bar for bar in expected_benchmark_bars if str(bar.symbol) == "SPY"),
                key=lambda bar: bar.timestamp,
            )
        )
        if not expected_benchmark:
            raise ValueError("strict_benchmark_source_missing")
        try:
            from scripts.research import r46_treasury_auction_unwind_tlt_v2_2026_09_10 as runner

            expected_benchmark_values = list(
                runner._benchmark_marks_at_timestamps(expected_benchmark, timestamps)
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("strict_benchmark_source_missing") from exc
        expected_benchmark_timestamps = timestamps
        if benchmark_timestamps != expected_benchmark_timestamps or any(
            abs(observed - expected) > _EPS
            for observed, expected in zip(benchmark, expected_benchmark_values, strict=True)
        ):
            raise ValueError("strict_benchmark_source_binding")
    benchmark_return = benchmark[-1] / benchmark[0] - 1.0
    if abs(_number(metrics.get("benchmark_return"), "strict_benchmark_return") - benchmark_return) > _EPS:
        raise ValueError("strict_benchmark_return")
    if abs(_number(metrics.get("excess_return"), "strict_excess_return") - (total_return - benchmark_return)) > _EPS:
        raise ValueError("strict_excess_return")

    returns = [
        curve[index] / curve[index - 1] - 1.0
        for index in range(1, len(curve))
        if curve[index - 1] > 0
    ]
    downside = [value for value in returns if value < 0]

    def sample_stddev(values: Sequence[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))

    std = sample_stddev(returns)
    downside_std = sample_stddev(downside)
    years = duration_days / 365.0
    annualizer = math.sqrt(max(1.0, len(returns) / years)) if returns and years > 0 else 1.0
    cagr = ((ending / starting) ** (1.0 / years) - 1.0) if years > 0 and ending > 0 else 0.0
    annualized_volatility = std * annualizer if std > 0 else 0.0
    sharpe = (sum(returns) / len(returns)) / std * annualizer if returns and std > 0 else 0.0
    sortino = (sum(returns) / len(returns)) / downside_std * annualizer if returns and downside_std > 0 else 0.0

    fills = [_mapping(value, "strict_metric_fill") for value in _sequence(inner.get("fills", ()), "strict_metric_fills")]
    terminal_rows = [
        _mapping(value, "strict_metric_terminal_row")
        for value in _sequence(inner.get("terminal_audit", ()), "strict_metric_terminal_audit")
    ]
    fee_total = sum(_number(fill.get("fee"), "strict_metric_fill_fee") for fill in fills) + sum(
        _number(row.get("fee"), "strict_metric_terminal_fee") for row in terminal_rows
    )
    slippage_total = sum(_number(fill.get("slippage_cost"), "strict_metric_fill_slippage") for fill in fills) + sum(
        _number(row.get("slippage_cost"), "strict_metric_terminal_slippage") for row in terminal_rows
    )
    spread_total = sum(_number(fill.get("spread_cost"), "strict_metric_fill_spread") for fill in fills) + sum(
        _number(row.get("spread_cost"), "strict_metric_terminal_spread") for row in terminal_rows
    )
    dividend_total = sum(
        _number(_mapping(value, "strict_metric_dividend").get("credited_cash", 0.0), "strict_metric_dividend_cash")
        for value in _sequence(inner.get("dividend_events", ()), "strict_metric_dividends")
    )
    avg_equity = sum(curve) / len(curve)
    turnover = (
        sum(
            _number(fill.get("qty"), "strict_metric_fill_qty")
            * _number(fill.get("reference_price"), "strict_metric_fill_reference")
            for fill in fills
        )
        / avg_equity
        if avg_equity > 0
        else 0.0
    )
    total_cost = fee_total + slippage_total + spread_total
    cost_ratio = total_cost / avg_equity if avg_equity > 0 else 0.0
    gross_return = (1.0 + total_return) / max(1e-9, 1.0 - cost_ratio) - 1.0 if cost_ratio < 1.0 else 0.0
    snapshots = [
        _mapping(value, "strict_metric_snapshot")
        for value in _sequence(inner.get("snapshots"), "strict_metric_snapshots")
    ]
    if not snapshots or len(snapshots) != len(curve):
        raise ValueError("strict_snapshots_missing")
    holdings_counts: list[float] = []
    cash_ratios: list[float] = []
    concentrations: list[float] = []
    history_raw = inner.get("holdings_history")
    history = (
        [_mapping(value, "strict_metric_holdings_history") for value in _sequence(history_raw, "strict_metric_holdings_history")]
        if history_raw is not None
        else []
    )
    trusted_metrics = (
        _mapping(trusted_replay.get("trusted_metrics"), "strict_trusted_replay_metrics")
        if trusted_replay is not None
        else None
    )
    trusted_observations = (
        _sequence(trusted_replay.get("metric_observations"), "strict_trusted_metric_observations")
        if trusted_replay is not None
        else []
    )
    if trusted_replay is None and history:
        raise ValueError("strict_holdings_history_untrusted")
    if trusted_replay is not None:
        if not history or len(history) != len(trusted_observations) or len(history) != len(curve):
            raise ValueError("strict_holdings_history_missing")
        for expected_timestamp, row, trusted_raw in zip(timestamps, history, trusted_observations, strict=True):
            trusted = _mapping(trusted_raw, "strict_trusted_metric_observation")
            if _timestamp(row.get("timestamp"), "strict_metric_holdings_timestamp") != expected_timestamp:
                raise ValueError("strict_holdings_history_binding")
            for key, field in (
                ("holdings_count", "strict_metric_holdings_count"),
                ("cash_ratio", "strict_metric_cash_ratio"),
                ("maximum_concentration", "strict_metric_holdings_concentration"),
            ):
                if abs(_number(row.get(key), field) - _number(trusted.get(key), field)) > _EPS:
                    raise ValueError("strict_holdings_history_untrusted")
                if key == "holdings_count":
                    holdings_counts.append(_number(trusted.get(key), field))
                elif key == "cash_ratio":
                    cash_ratios.append(_number(trusted.get(key), field))
                else:
                    concentrations.append(_number(trusted.get(key), field))
    elif history:
        if len(history) != len(curve):
            raise ValueError("strict_holdings_history_missing")
        for expected_timestamp, row in zip(timestamps, history, strict=True):
            if _timestamp(row.get("timestamp"), "strict_metric_holdings_timestamp") != expected_timestamp:
                raise ValueError("strict_holdings_history_binding")
            holdings_counts.append(_number(row.get("holdings_count"), "strict_metric_holdings_count"))
            cash_ratios.append(_number(row.get("cash_ratio"), "strict_metric_cash_ratio"))
            concentrations.append(
                _number(row.get("maximum_concentration"), "strict_metric_holdings_concentration")
            )
    for snapshot in snapshots:
        if history:
            break
        equity = _number(snapshot.get("equity"), "strict_metric_snapshot_equity")
        if equity <= 0:
            raise ValueError("strict_metric_snapshot_equity")
        positions = _mapping(snapshot.get("positions"), "strict_metric_snapshot_positions")
        if "holdings_count" in snapshot:
            holdings_counts.append(_number(snapshot.get("holdings_count"), "strict_metric_holdings_count"))
        else:
            holdings_counts.append(
                sum(
                    _number(_mapping(value, "strict_metric_position").get("qty"), "strict_metric_position_qty") > _EPS
                    for value in positions.values()
                )
            )
        cash_ratios.append(
            _number(snapshot.get("cash_weight"), "strict_metric_cash_weight")
            if "cash_weight" in snapshot
            else _number(snapshot.get("cash"), "strict_metric_snapshot_cash") / equity
        )
        concentrations.append(
            _number(snapshot.get("maximum_concentration"), "strict_metric_snapshot_concentration")
            if "maximum_concentration" in snapshot
            else max(
                (
                    _number(_mapping(value, "strict_metric_position").get("value"), "strict_metric_position_value")
                    / equity
                    for value in positions.values()
                ),
                default=0.0,
                )
            )
    closed_positions = 0
    quantities: dict[str, float] = {}
    for fill in fills:
        symbol = str(fill.get("symbol", ""))
        quantity = _number(fill.get("qty"), "strict_metric_fill_qty")
        if str(fill.get("side", "")).upper() == "BUY":
            quantities[symbol] = quantities.get(symbol, 0.0) + quantity
        elif str(fill.get("side", "")).upper() == "SELL":
            remaining = quantities.get(symbol, 0.0) - quantity
            if remaining <= _EPS and quantities.get(symbol, 0.0) > _EPS:
                closed_positions += 1
            quantities[symbol] = max(0.0, remaining)
    closed_positions += sum(row.get("position_removed") is True for row in terminal_rows)
    expected: dict[str, float | int] = {
        "gross_return": gross_return,
        "cagr": cagr,
        "annualized_volatility": annualized_volatility,
        "sharpe": sharpe,
        "sortino": sortino,
        "turnover": turnover,
        "fee_total": fee_total,
        "slippage_cost_total": slippage_total,
        "spread_cost_total": spread_total,
        "dividend_total": dividend_total,
        "average_holdings_count": sum(holdings_counts) / len(holdings_counts),
        "average_cash_ratio": sum(cash_ratios) / len(cash_ratios),
        "maximum_concentration": max(concentrations, default=0.0),
        "fill_count": len(fills),
        "rebalance_count": len(_sequence(inner.get("rebalances", ()), "strict_metric_rebalances")),
        "closed_positions": closed_positions,
    }
    if trusted_metrics is not None:
        for key in (
            "average_holdings_count",
            "average_cash_ratio",
            "maximum_concentration",
            "fill_count",
            "closed_positions",
            "rebalance_count",
            "fee_total",
            "slippage_cost_total",
            "spread_cost_total",
            "dividend_total",
        ):
            if key not in trusted_metrics:
                raise ValueError(f"strict_trusted_metric_missing:{key}")
            expected[key] = trusted_metrics[key]
    for key, expected_value in expected.items():
        if key not in metrics:
            raise ValueError(f"strict_{key}_missing")
        if isinstance(expected_value, int):
            if metrics.get(key) != expected_value:
                raise ValueError(f"strict_{key}")
        elif abs(_number(metrics.get(key), f"strict_{key}") - expected_value) > _EPS:
            raise ValueError(f"strict_{key}")


def _strict_recompute_timing_placebo(
    *,
    timing: Mapping[str, Any],
    package_inputs: Any,
    incumbent: Any,
    bars: Mapping[tuple[str, datetime], Any],
) -> dict[str, Any]:
    """Replay persisted shifted cells; never accept the result's placebo map."""

    try:
        from scripts.research import r46_treasury_auction_unwind_tlt_v2_2026_09_10 as runner

        candidate = _mapping(timing.get("candidate_ledger"), "strict_placebo_candidate_ledger")
        control = _mapping(timing.get("control_ledger"), "strict_placebo_control_ledger")
        binding = _mapping(timing.get("source_binding"), "strict_placebo_source_binding")
        shifted = runner._shift_r46_package_for_placebo(package_inputs)
        dataset = runner.build_r46_market_dataset(shifted, incumbent)
        _verify_economic_structural(candidate)
        _verify_economic_structural(control)
        if (
            candidate.get("arm") != "candidate"
            or candidate.get("control") is not False
            or control.get("arm") != "control"
            or control.get("control") is not True
            or candidate.get("cost_multiplier") != 1.0
            or control.get("cost_multiplier") != 1.0
            or candidate.get("manifest_canonical_sha256") != control.get("manifest_canonical_sha256")
            or candidate.get("package_canonical_sha256") != control.get("package_canonical_sha256")
            or candidate.get("preflight_receipt_sha256") != control.get("preflight_receipt_sha256")
            or candidate.get("universe_status") != control.get("universe_status")
            or candidate.get("universe_version") != control.get("universe_version")
        ):
            raise ValueError("strict_placebo_ledger_linkage")
        expected_schedules = [schedule.to_dict() for schedule in shifted.schedules]
        expected_markers = [
            {
                "timestamp": marker.timestamp.isoformat(),
                "symbol": marker.symbol,
                "source": shifted.marker_sources.get(
                    next(
                        (item.event_id for item in shifted.schedules if item.entry_decision == marker.timestamp),
                        "",
                    ),
                ),
            }
            for marker in shifted.markers
        ]
        for label, ledger in (("candidate", candidate), ("control", control)):
            if list(_sequence(ledger.get("event_schedules"), f"strict_placebo_{label}_schedules")) != expected_schedules:
                raise ValueError("strict_placebo_schedule_multiplicity")
            if list(_sequence(ledger.get("marker_records"), f"strict_placebo_{label}_markers")) != expected_markers:
                raise ValueError("strict_placebo_marker_multiplicity")
        if candidate.get("event_records") is None or control.get("event_records") != []:
            raise ValueError("strict_placebo_event_record_linkage")
        if control.get("event_fill_statuses") != []:
            raise ValueError("strict_placebo_control_status_linkage")
        expected_schedule_digest = hashlib.sha256(
            _canonical_bytes({"schedules": [schedule.to_dict() for schedule in shifted.schedules]})
        ).hexdigest()
        expected_marker_digest = hashlib.sha256(
            _canonical_bytes(
                {
                    "markers": [
                        {"symbol": marker.symbol, "timestamp": marker.timestamp.isoformat(), "close": marker.close}
                        for marker in shifted.markers
                    ]
                }
            )
        ).hexdigest()
        if (
            timing.get("shift_sessions") != runner.PLACEBO_SHIFT_SESSIONS
            or timing.get("seed") != runner.PLACEBO_RANDOM_SEED
            or timing.get("engine_invocations") != 2
            or binding.get("shift_sessions") != runner.PLACEBO_SHIFT_SESSIONS
            or binding.get("shifted_dataset_digest") != dataset.content_digest()
            or binding.get("shifted_schedule_digest") != expected_schedule_digest
            or binding.get("shifted_marker_digest") != expected_marker_digest
            or binding.get("manifest_canonical_sha256") != candidate.get("manifest_canonical_sha256")
            or binding.get("package_canonical_sha256") != candidate.get("package_canonical_sha256")
            or binding.get("preflight_receipt_sha256") != candidate.get("preflight_receipt_sha256")
            or binding.get("source_paths") != candidate.get("source_paths")
            or candidate.get("source_paths") != control.get("source_paths")
            or binding.get("universe_status") != candidate.get("universe_status")
            or binding.get("universe_version") != candidate.get("universe_version")
            or candidate.get("dataset_digest") != dataset.content_digest()
            or control.get("dataset_digest") != dataset.content_digest()
        ):
            raise ValueError("strict_placebo_source_binding")
        timing_bundle = {
            "cells": [
                {
                    "cost_multiplier": 1.0,
                    "candidate": candidate,
                    "control": control,
                }
            ]
        }
        shifted_bars: dict[tuple[str, datetime], Any] = {}
        for bar in dataset.executable_bars:
            bar_key = (str(bar.symbol), bar.timestamp)
            if bar_key in shifted_bars:
                raise ValueError("strict_placebo_duplicate_source_bar")
            shifted_bars[bar_key] = bar
        shifted_benchmark_bars = tuple(
            sorted(
                (bar for bar in dataset.signal_bars if str(bar.symbol) == "SPY"),
                key=lambda bar: bar.timestamp,
            )
        )
        shifted_curve_timestamps = tuple(sorted({bar.timestamp for bar in dataset.executable_bars}))
        _strict_verify_ledger_sources(
            package_inputs=shifted,
            incumbent=incumbent,
            bars=shifted_bars,
            ledger=timing_bundle,
            benchmark_bars=shifted_benchmark_bars,
            expected_curve_timestamps=shifted_curve_timestamps,
        )
        values = runner._event_episode_deltas(shifted, candidate, cost_multiplier=1.0)
        permutation = runner._label_permutation(values, seed=runner.PLACEBO_RANDOM_SEED)
        candidate_metrics = _mapping(candidate.get("result_metrics"), "strict_placebo_candidate_metrics")
        control_metrics = _mapping(control.get("result_metrics"), "strict_placebo_control_metrics")
        declared_candidate = _mapping(timing.get("candidate_metrics"), "strict_placebo_declared_candidate_metrics")
        declared_control = _mapping(timing.get("control_metrics"), "strict_placebo_declared_control_metrics")
        if dict(declared_candidate) != dict(candidate_metrics) or dict(declared_control) != dict(control_metrics):
            raise ValueError("strict_placebo_metrics_crosslink")
        delta = {
            key: _number(candidate_metrics.get(key), f"strict_placebo_candidate_{key}")
            - _number(control_metrics.get(key), f"strict_placebo_control_{key}")
            for key in ("ending_equity", "total_return", "net_return", "fee_total", "dividend_total")
        }
        declared_delta = _mapping(timing.get("candidate_minus_control"), "strict_placebo_delta")
        for metric_key, expected in delta.items():
            if abs(_number(declared_delta.get(metric_key), f"strict_placebo_delta_{metric_key}") - expected) > _EPS:
                raise ValueError("strict_placebo_delta")
        declared_values = _sequence(timing.get("event_episode_deltas"), "strict_placebo_event_deltas")
        if len(declared_values) != len(values) or any(
            abs(_number(observed, "strict_placebo_event_delta") - expected) > _EPS
            for observed, expected in zip(declared_values, values, strict=True)
        ):
            raise ValueError("strict_placebo_event_deltas")
        if timing.get("event_episode_count") != len(values):
            raise ValueError("strict_placebo_event_count")
        if timing.get("status") == "REPORTED_NATIVE_ENGINE_DIAGNOSTIC_ONLY":
            if timing.get("p_value") != permutation.get("p_value") or timing.get("permutation") != permutation:
                raise ValueError("strict_placebo_permutation")
        elif timing.get("status") == "BLOCKED_TIMING_PLACEBO_NO_COMPLETE_EPISODES":
            if values or timing.get("p_value") is not None:
                raise ValueError("strict_placebo_blocked_status")
        else:
            raise ValueError("strict_placebo_status")
        verified = dict(timing)
        verified["candidate_metrics"] = dict(candidate_metrics)
        verified["control_metrics"] = dict(control_metrics)
        verified["candidate_minus_control"] = delta
        verified["event_episode_deltas"] = list(values)
        verified["event_episode_count"] = len(values)
        if timing.get("status") == "REPORTED_NATIVE_ENGINE_DIAGNOSTIC_ONLY":
            verified["permutation"] = permutation
            verified["p_value"] = permutation.get("p_value")
        return verified
    except Exception as exc:
        raise ValueError("strict_placebo_recompute") from exc


def _strict_verify_statistics(
    *,
    results: Mapping[str, Any],
    ledger: Mapping[str, Any],
    package_inputs: Any,
    incumbent: Any,
    bars: Mapping[tuple[str, datetime], Any],
    trusted_event_replays: Mapping[str, Mapping[str, object]] | None = None,
) -> None:
    try:
        from scripts.research import r46_treasury_auction_unwind_tlt_v2_2026_09_10 as runner

        result_cells = _sequence(results.get("cells"), "strict_result_cells")
        ledger_cells = _sequence(ledger.get("cells"), "strict_ledger_cells")
        cells: list[dict[str, Any]] = []
        for result_raw, ledger_raw in zip(result_cells, ledger_cells, strict=True):
            result_cell = _mapping(result_raw, "strict_result_cell")
            ledger_cell = _mapping(ledger_raw, "strict_ledger_cell")
            multiplier = _number(result_cell.get("cost_multiplier"), "strict_multiplier")
            candidate_ledger = dict(_mapping(ledger_cell.get("candidate"), "strict_candidate_ledger"))
            trusted_replay = (trusted_event_replays or {}).get(f"{multiplier:.12g}:candidate")
            if trusted_replay is not None:
                candidate_ledger["event_records"] = _sequence(
                    trusted_replay.get("event_records", ()), "strict_trusted_event_records"
                )
                candidate_ledger["event_fill_statuses"] = _sequence(
                    trusted_replay.get("event_fill_statuses", ()), "strict_trusted_event_fill_statuses"
                )
            cells.append(
                {
                    "cost_multiplier": multiplier,
                    "candidate_metrics": _mapping(result_cell.get("candidate_metrics"), "strict_candidate_metrics"),
                    "control_metrics": _mapping(result_cell.get("control_metrics"), "strict_control_metrics"),
                    "candidate_ledger": candidate_ledger,
                    "control_ledger": dict(_mapping(ledger_cell.get("control"), "strict_control_ledger")),
                }
            )
        actual = _mapping(results.get("statistics"), "strict_statistics")
        verified_placebo = _strict_recompute_timing_placebo(
            timing=_mapping(actual.get("timing_placebo_plus_10_sessions"), "strict_placebo"),
            package_inputs=package_inputs,
            incumbent=incumbent,
            bars=bars,
        )
        expected = runner._build_statistics(
            package_inputs,
            cells,
            timing_placebo_cell=verified_placebo,
        )
    except Exception as exc:
        raise ValueError("strict_statistics_recompute") from exc
    for key in (
        "primary_estimand",
        "unchanged_incumbent_comparison",
        "timing_placebo_plus_10_sessions",
        "cost_outputs",
        "capacity",
        "missingness",
        "resampling",
        "holm",
    ):
        if actual.get(key) != expected.get(key):
            raise ValueError(f"strict_statistics_crosslink:{key}")
    actual_coverage = _mapping(actual.get("coverage_gates"), "strict_coverage")
    expected_coverage = _mapping(expected.get("coverage_gates"), "strict_expected_coverage")
    for key in (
        "minimum_event_episodes",
        "minimum_holding_intervals",
        "maximum_missing_rate",
        "maximum_capacity_suppression_rate",
        "event_episodes",
        "holding_intervals",
        "missing_rate",
        "capacity_suppression_rate",
        "fifth_open_exit_complete",
        "fifth_open_exit_count",
        "sixth_future_open_complete",
    ):
        if actual_coverage.get(key) != expected_coverage.get(key):
            raise ValueError(f"strict_coverage_recompute:{key}")
    if results.get("status") == "PASS_RESEARCH_ONLY":
        rows = _sequence(_mapping(actual.get("primary_estimand"), "strict_primary").get("rows"), "strict_primary_rows")
        if not rows or int(actual_coverage.get("event_episodes", 0)) <= 0:
            raise ValueError("strict_pass_empty_statistics")
        if actual_coverage.get("status") != "PASS" or actual_coverage.get("blockers") != []:
            raise ValueError("strict_pass_coverage_status")


def _strict_one_shot_chain(
    *,
    root: Path,
    manifest: Mapping[str, Any],
    results: Mapping[str, Any],
    ledger: Mapping[str, Any],
    receipt: Mapping[str, Any],
    output_dir: Path,
    receipt_path: Path,
    bundle_hash: str,
    gate_hash: str,
    manifest_hash: str,
    package_hash: str,
) -> None:
    evaluation = manifest.get("evaluation_contract")
    integrity = manifest.get("integrity")
    if not isinstance(evaluation, Mapping) or not isinstance(integrity, Mapping):
        raise ValueError("strict_one_shot_contract_missing")
    state_reference = evaluation.get("one_shot_state_path")
    if state_reference != integrity.get("one_shot_state_path") or not isinstance(state_reference, str):
        raise ValueError("strict_one_shot_path_crosslink")
    state_path = _strict_contained_file(root, state_reference, "one_shot_state", relative_only=True)
    state = _strict_artifact(state_path, "one_shot_state", schema="r46-one-shot-state-v2", integrity_field="artifact_sha256")
    if state.get("research_id") != RESEARCH_ID or state.get("candidate_id") != "R46" or state.get("status") != "SEALED_UNUSED":
        raise ValueError("strict_one_shot_state_identity")
    declared_state_canonical = evaluation.get("one_shot_state_sha256")
    if declared_state_canonical != state.get("integrity", {}).get("artifact_sha256"):
        raise ValueError("strict_one_shot_state_hash")
    state_raw_hash = _sha256_file(state_path)
    for payload, field in ((results, "results"), (ledger, "ledger")):
        if payload.get("one_shot_state_path") != state_reference or payload.get("one_shot_state_sha256") != state_raw_hash:
            raise ValueError(f"strict_one_shot_{field}_crosslink")
    one_shot = receipt.get("one_shot")
    if not isinstance(one_shot, Mapping) or one_shot.get("status") != "CONSUMED":
        raise ValueError("strict_one_shot_receipt_status")
    receipt_state_reference = one_shot.get("state_path")
    if receipt_state_reference != state_reference or not isinstance(receipt_state_reference, str) or Path(receipt_state_reference).is_absolute():
        raise ValueError("strict_one_shot_receipt_path")
    consumed_path = state_path.with_name(f"{state_path.name}.consumed.json")
    reserved_path = state_path.with_name(f"{state_path.name}.reserved.json")
    if state_path.with_name(f"{state_path.name}.aborted_consumed.json").exists():
        raise ValueError("strict_one_shot_aborted")
    reserved_path = _strict_contained_file(
        root,
        str(reserved_path.relative_to(root.resolve())),
        "one_shot_reserved_transition",
        relative_only=True,
    )
    consumed_path = _strict_contained_file(
        root,
        str(consumed_path.relative_to(root.resolve())),
        "one_shot_consumed_transition",
        relative_only=True,
    )
    reserved = _strict_artifact(
        reserved_path,
        "one_shot_reserved_transition",
        schema="r46-one-shot-transition-v2",
        integrity_field="transition_sha256",
    )
    consumed = _strict_artifact(
        consumed_path,
        "one_shot_consumed_transition",
        schema="r46-one-shot-transition-v2",
        integrity_field="transition_sha256",
    )
    for transition, status in ((reserved, "RESERVED"), (consumed, "CONSUMED")):
        transition_state = transition.get("state_path")
        if not isinstance(transition_state, str) or Path(transition_state).resolve() != state_path:
            raise ValueError("strict_one_shot_transition_state")
        if transition.get("state_sha256") != state_raw_hash or transition.get("state_canonical_sha256") != declared_state_canonical:
            raise ValueError("strict_one_shot_transition_state_hash")
        if transition.get("gate_sha256") != gate_hash or transition.get("manifest_sha256") != manifest_hash or transition.get("package_sha256") != package_hash:
            raise ValueError("strict_one_shot_transition_sources")
        if transition.get("status") != status:
            raise ValueError("strict_one_shot_transition_status")
    reserved_hash = reserved.get("integrity", {}).get("transition_sha256")
    if consumed.get("reserved_transition_sha256") != reserved_hash:
        raise ValueError("strict_one_shot_transition_chain")
    if consumed.get("output_sha256") != bundle_hash or one_shot.get("output_sha256") != bundle_hash:
        raise ValueError("strict_one_shot_output_hash")
    if Path(str(consumed.get("output_path", ""))).resolve() != output_dir.resolve() or Path(
        str(consumed.get("receipt_path", ""))
    ).resolve() != receipt_path.resolve():
        raise ValueError("strict_one_shot_output_path")


def verify_economic_run_from_files(
    repo_root: Path | str,
    results_path: Path | str,
    ledger_path: Path | str,
    report_path: Path | str,
    run_receipt_path: Path | str,
    preflight_receipt_path: Path | str,
) -> dict[str, object]:
    """Strict, read-only verification of the published R46 v2 output bundle.

    The in-memory ``verify_economic_run`` function is retained for the runner's
    pre-publication structural check and for explicitly synthetic tests.  This
    API is the final gate: every path is caller-explicit and contained, the
    persisted PRE_RUN_OK receipt chooses the governance/package artifacts, and
    all source and output hashes are checked against bytes on disk before any
    economic field is considered authoritative.
    """

    root = Path(repo_root).resolve()
    results_file = _strict_contained_file(root, str(results_path), "results")
    ledger_file = _strict_contained_file(root, str(ledger_path), "ledger")
    report_file = _strict_contained_file(root, str(report_path), "report")
    receipt_file = _strict_contained_file(root, str(run_receipt_path), "run_receipt")
    gate_file = _strict_contained_file(root, str(preflight_receipt_path), "preflight_receipt")
    output_dir = results_file.parent
    if any(path.parent != output_dir for path in (ledger_file, report_file, receipt_file)):
        raise ValueError("strict_output_directory_crosslink")
    if (results_file.name, ledger_file.name, report_file.name, receipt_file.name) != (
        "results.json",
        "ledger.json",
        "report.json",
        "run_receipt.json",
    ):
        raise ValueError("strict_output_filename_contract")
    results = _strict_json(results_file, "results")
    ledger = _strict_json(ledger_file, "ledger")
    report = _strict_json(report_file, "report")
    receipt = _strict_json(receipt_file, "run_receipt")
    if receipt.get("status") not in {"PASS_RESEARCH_ONLY", "FAILED_VALID_FROZEN_TEST"}:
        raise ValueError("strict_run_receipt_status")
    if receipt.get("test_only") is not False:
        raise ValueError("strict_test_only_receipt_forbidden")
    outputs = _mapping(receipt.get("outputs"), "strict_outputs")
    raw_output_hashes = {
        "results_sha256": _sha256_file(results_file),
        "ledger_sha256": _sha256_file(ledger_file),
        "report_sha256": _sha256_file(report_file),
    }
    for field, path in (("results_sha256", results_file), ("ledger_sha256", ledger_file), ("report_sha256", report_file)):
        if outputs.get(field) != raw_output_hashes[field] or outputs.get(field) != _published_payload_hash(_strict_json(path, field)):
            raise ValueError("strict_output_raw_hash")
    bundle_hash = hashlib.sha256(
        _canonical_bytes(
            {
                "results_sha256": raw_output_hashes["results_sha256"],
                "ledger_sha256": raw_output_hashes["ledger_sha256"],
                "report_sha256": raw_output_hashes["report_sha256"],
            }
        )
    ).hexdigest()
    if outputs.get("bundle_sha256") != bundle_hash:
        raise ValueError("strict_output_bundle_hash")
    if receipt.get("receipt_sha256") != _canonical_hash(receipt, "receipt_sha256"):
        raise ValueError("strict_run_receipt_hash")
    if receipt.get("one_shot", {}).get("output_sha256") != bundle_hash:
        raise ValueError("strict_receipt_output_crosslink")
    if receipt.get("one_shot", {}).get("status") != "CONSUMED":
        raise ValueError("strict_run_not_consumed")
    if results.get("preflight_receipt_path") != _strict_relative(root, gate_file, "preflight_receipt"):
        raise ValueError("strict_preflight_path_crosslink")
    if receipt.get("preflight_receipt_path") != _strict_relative(root, gate_file, "preflight_receipt"):
        raise ValueError("strict_receipt_preflight_path")
    if receipt.get("status") != results.get("status"):
        raise ValueError("strict_receipt_status_crosslink")
    manifest_path, package_path, manifest, package, gate = _strict_preflight_chain(
        root=root,
        preflight_receipt_path=gate_file,
        expected_manifest_hash=results.get("manifest_canonical_sha256"),
        expected_package_hash=results.get("package_canonical_sha256"),
        expected_gate_hash=results.get("preflight_receipt_sha256"),
    )[1:]
    if receipt.get("governance_manifest_path") != _strict_relative(root, manifest_path, "manifest"):
        raise ValueError("strict_receipt_manifest_path")
    if receipt.get("data_package_manifest_path") != _strict_relative(root, package_path, "package"):
        raise ValueError("strict_receipt_package_path")
    if receipt.get("manifest_canonical_sha256") != results.get("manifest_canonical_sha256") or receipt.get("package_canonical_sha256") != results.get("package_canonical_sha256"):
        raise ValueError("strict_receipt_manifest_package_hash")
    if results.get("source_commit") != receipt.get("source_commit") or results.get("freeze_commit") != receipt.get("freeze_commit"):
        raise ValueError("strict_receipt_commit_crosslink")
    source_paths = _strict_source_pins(root=root, manifest=manifest, results=results, ledger=ledger, receipt=receipt)
    _strict_one_shot_chain(
        root=root,
        manifest=manifest,
        results=results,
        ledger=ledger,
        receipt=receipt,
        output_dir=output_dir,
        receipt_path=receipt_file,
        bundle_hash=bundle_hash,
        gate_hash=str(results.get("preflight_receipt_sha256")),
        manifest_hash=str(results.get("manifest_canonical_sha256")),
        package_hash=str(results.get("package_canonical_sha256")),
    )
    del source_paths, gate
    _verify_boundary(results, economic=True)
    _verify_boundary(ledger, economic=True)
    _verify_boundary(report, economic=True)
    # This call intentionally remains after physical/hash checks.  It is a
    # structural consistency check, never the source of authority.
    verify_economic_run(results, ledger, report, receipt)
    _strict_package, package_inputs, incumbent, bars, benchmark_bars, curve_timestamps = _strict_source_dataset(
        root=root, package_path=package_path, results=results, ledger_bundle=ledger
    )
    trusted_event_replays = _strict_verify_ledger_sources(
        package_inputs=package_inputs,
        incumbent=incumbent,
        bars=bars,
        ledger=ledger,
        benchmark_bars=benchmark_bars,
        expected_curve_timestamps=curve_timestamps,
    )
    _strict_verify_statistics(
        results=results,
        ledger=ledger,
        package_inputs=package_inputs,
        incumbent=incumbent,
        bars=bars,
        trusted_event_replays=trusted_event_replays,
    )
    for key in (
        "primary_estimand",
        "unchanged_incumbent_comparison",
        "timing_placebo_plus_10_sessions",
        "cost_outputs",
        "capacity",
        "missingness",
        "coverage_gates",
        "resampling",
        "holm",
        "statistics_pins",
    ):
        if report.get(key) != results.get("statistics", {}).get(key):
            raise ValueError(f"strict_report_crosslink:{key}")
    if results.get("source_paths") != ledger.get("cells", [{}])[0].get("candidate", {}).get("source_paths"):
        raise ValueError("strict_result_source_paths_crosslink")
    return {
        "status": "VERIFIED_ECONOMIC_RUN_FROM_FILES",
        "schema_version": results["schema_version"],
        "output_bundle_sha256": bundle_hash,
        "cost_cell_count": 3,
        "promotion": False,
    }


def verify_economic_package(
    output_dir: Path | str,
    *,
    repo_root: Path | str | None = None,
) -> dict[str, object]:
    """Convenience wrapper for the canonical five-file output directory."""

    directory = Path(output_dir).resolve()
    required = tuple(directory / name for name in ("results.json", "ledger.json", "report.json", "run_receipt.json"))
    if any(not path.is_file() for path in required):
        raise ValueError("economic_output_files_missing")
    root = Path(repo_root or REPO).resolve()
    gate_hint = directory / "preflight_receipt.json"
    if not gate_hint.is_file():
        try:
            receipt = _strict_json(required[3], "run_receipt")
            gate_reference = receipt.get("preflight_receipt_path")
            gate_hint = _strict_contained_file(root, gate_reference, "preflight_receipt", relative_only=True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("economic_preflight_receipt_missing") from exc
    return verify_economic_run_from_files(root, required[0], required[1], required[2], required[3], gate_hint)


def verify_economic_payloads_test_only(
    results: Mapping[str, Any],
    ledger_bundle: Mapping[str, Any],
    report: Mapping[str, Any] | None = None,
    run_receipt: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Explicit pre-publication/test-fixture structural verification only."""

    return verify_economic_run(results, ledger_bundle, report, run_receipt)


def _verify_forbidden_economic_keys(value: object, *, allow_fills: bool = False) -> None:
    forbidden = {"returns", "return", "nav", "pnl", "p&l", "turnover", "drawdown", "cvar", "sharpe", "sortino"}
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and key.casefold() in forbidden:
                raise ValueError("attestation_outcome_fields")
            if key == "fills" and allow_fills:
                continue
            _verify_forbidden_economic_keys(child, allow_fills=allow_fills)
    elif isinstance(value, list):
        for child in value:
            _verify_forbidden_economic_keys(child, allow_fills=allow_fills)


def verify_payload(payload: Mapping[str, Any]) -> dict[str, object]:
    """Verify a serialized inner ledger without importing the runner."""

    if not isinstance(payload, Mapping):
        raise ValueError("ledger_invalid")
    if payload.get("schema_version") != NATIVE_LEDGER_SCHEMA:
        raise ValueError("ledger_schema_or_status")
    expected_hash = payload.get("ledger_sha256")
    if not isinstance(expected_hash, str) or expected_hash != _canonical_hash(payload, "ledger_sha256"):
        raise ValueError("LEDGER_HASH_MISMATCH")
    if payload.get("status") == ECONOMIC_LEDGER_STATUS:
        return _verify_economic_structural(payload)
    _verify_boundary(payload)
    caps = _verify_caps(payload)
    if payload.get("status") == NATIVE_SMOKE_STATUS:
        return _verify_native_structural(payload)
    if payload.get("status") != SYNTHETIC_STATUS or payload.get("test_only_synthetic") is not True:
        raise ValueError("ledger_schema_or_status")
    _verify_decisions(payload, caps)
    positions = _verify_initial_positions(payload)
    expected_cash, total_fees, fills = _verify_synthetic_fills(payload, positions)
    _verify_rejections(payload)
    _verify_dividends(payload, native=False)
    expected_cash += _number(payload.get("dividend_total", 0.0), "dividend_total")
    if abs(_number(payload.get("cash"), "cash") - expected_cash) > _EPS:
        raise ValueError("cash_reconciliation")
    if abs(_number(payload.get("fees"), "fees") - total_fees) > _EPS:
        raise ValueError("fee_reconciliation")
    _verify_synthetic_snapshots(payload, positions, expected_cash, total_fees, caps)
    unfilled = _sequence(payload.get("unfilled_exits"), "unfilled_exits")
    for item in unfilled:
        row = _mapping(item, "unfilled_exit")
        if not str(row.get("reason", "")).strip() or not str(row.get("owner", "")).strip():
            raise ValueError("unfilled_exit_fields_missing")
    return {
        "status": "VERIFIED",
        "schema_version": payload["schema_version"],
        "fill_count": len(fills),
        "snapshot_count": len(_sequence(payload.get("snapshots"), "snapshots")),
        "unfilled_exit_count": len(unfilled),
        "research_only": True,
        "promotion": False,
    }


def verify_attestation(payload: Mapping[str, Any], *, repo_root: Path | None = None) -> dict[str, object]:
    """Verify the outer native structural receipt and its inner ledger."""

    if not isinstance(payload, Mapping) or payload.get("schema_version") != ATTESTATION_SCHEMA:
        raise ValueError("attestation_schema")
    if payload.get("status") != "READY_NATIVE_LEDGER":
        raise ValueError("attestation_not_ready")
    if payload.get("research_id") != RESEARCH_ID or payload.get("candidate_id") != "R46":
        raise ValueError("attestation_identity")
    if payload.get("native_adapter_verified") is not True or payload.get("native_engine") != "PortfolioBacktestEngine":
        raise ValueError("attestation_native_adapter_required")
    _verify_file_pins(payload, repo_root=(repo_root or REPO).resolve())
    expected_hash = payload.get("attestation_sha256")
    if not isinstance(expected_hash, str) or expected_hash != _canonical_hash(payload, "attestation_sha256"):
        raise ValueError("ATTESTATION_HASH_MISMATCH")
    _verify_boundary(payload)
    for field in (
        "governance_canonical_sha256",
        "package_canonical_sha256",
        "runner_sha256",
        "verifier_sha256",
        "source_sha256",
        "manifest_sha256",
        "package_sha256",
    ):
        if not _valid_hex64(payload.get(field)):
            raise ValueError("attestation_source_pin_invalid")
    ledger = _mapping(payload.get("ledger"), "ledger")
    verified = verify_payload(ledger)
    if payload.get("fills") != ledger.get("fills"):
        raise ValueError("attestation_fill_crosslink")
    if payload.get("native_one_ledger") is not True or payload.get("separate_sleeve_blend") is not False:
        raise ValueError("attestation_one_ledger_required")
    _verify_forbidden_economic_keys(payload, allow_fills=True)
    return {
        "status": "VERIFIED_NATIVE_LEDGER",
        "schema_version": payload["schema_version"],
        "fill_count": verified.get("fill_count", 0),
        "native_engine": payload["native_engine"],
        "promotion": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--attestation", action="store_true")
    parser.add_argument(
        "--economic-final",
        nargs=5,
        metavar=("RESULTS", "LEDGER", "REPORT", "RUN_RECEIPT", "PREFLIGHT_RECEIPT"),
        help="strict read-only final verification; input is the repository root",
    )
    args = parser.parse_args()
    try:
        if args.economic_final:
            result = verify_economic_run_from_files(args.input, *args.economic_final)
        else:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            result = verify_attestation(payload) if args.attestation else verify_payload(payload)
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "verify_attestation",
    "verify_economic_run",
    "verify_economic_run_from_files",
    "verify_economic_package",
    "verify_economic_payloads_test_only",
    "verify_payload",
]
