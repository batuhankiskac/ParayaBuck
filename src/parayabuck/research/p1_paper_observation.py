"""Read-only, epoch-bound P1 natural PAPER observation aggregation."""

from __future__ import annotations

import hashlib
import json
import math
import random
import sqlite3
import statistics
from calendar import monthrange
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from parayabuck.execution.shadow_comparator import run_shadow_comparator
from parayabuck.execution.telemetry import execution_measurement
from parayabuck.portfolio.runtime_models import is_terminal_order_status

_MARKETS = ("equity", "crypto")
_WINDOW_DAYS = (7, 30, 90)
_ACCOUNT_HASH_DOMAIN = b"parayabuck-p1-account-v1\0"
_FEE_LIMITATION = "event-local fee evidence is not an authoritative reconciled fee activity"
_MANIFEST = Path.cwd() / "data/research_manifests/execution_cost_transferability_p1_paper_observation_2026_09.json"


def account_identity_hash(raw_account_id: str) -> str:
    """Return the P1-only, domain-separated account identity commitment."""
    return hashlib.sha256(_ACCOUNT_HASH_DOMAIN + raw_account_id.encode("utf-8")).hexdigest()


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _number(value: object) -> float | None:
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _summary(values: Iterable[float]) -> dict[str, object]:
    rows = sorted(values)
    if not rows:
        return {"count": 0, "mean": None, "median": None, "p75": None, "p95": None}

    def percentile(percent: float) -> float:
        rank = (len(rows) - 1) * percent
        low = int(rank)
        high = min(low + 1, len(rows) - 1)
        return rows[low] * (high - rank) + rows[high] * (rank - low)

    return {
        "count": len(rows),
        "mean": statistics.fmean(rows),
        "median": statistics.median(rows),
        "p75": percentile(0.75),
        "p95": percentile(0.95),
    }


def _open_readonly(path: str | Path) -> sqlite3.Connection:
    source = Path(path).resolve()
    if not source.is_file():
        raise ValueError("runtime database path is not a regular file")
    connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _load_manifest(path: Path) -> tuple[dict[str, object], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("P1 manifest is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("status") != "planned":
        raise ValueError("P1 manifest is malformed or not planned")
    gates = payload.get("minimum_sample_gates")
    if not isinstance(gates, Mapping) or set(_MARKETS) - set(gates):
        raise ValueError("P1 manifest lacks per-market minimum sample gates")
    for market in _MARKETS:
        required = gates.get(market)
        if not isinstance(required, Mapping) or not all(
            isinstance(value, int) and value >= 0 for value in required.values()
        ):
            raise ValueError("P1 manifest has malformed minimum sample gates")
    return payload, hashlib.sha256(path.read_bytes()).hexdigest()


def _lineage(
    path: str | Path,
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]], dict[str, list[dict[str, object]]]]:
    connection = _open_readonly(path)
    try:
        required = {
            "portfolio_runtime_sessions",
            "portfolio_decisions",
            "portfolio_orders",
            "portfolio_execution_events",
        }
        available = {
            str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if not required.issubset(available):
            return {}, {}, {}
        sessions = {
            str(row["session_id"]): dict(row)
            for row in connection.execute(
                "SELECT session_id, account_id, configuration_digest, mode, started_at FROM portfolio_runtime_sessions"
            )
        }
        orders = {
            str(row["client_order_id"]): dict(row)
            for row in connection.execute(
                """SELECT o.client_order_id, d.decision_id, d.session_id, d.account_id, d.configuration_digest, d.decision_timestamp, d.market, d.strategy_name FROM portfolio_orders o JOIN portfolio_decisions d ON d.decision_id=o.decision_id"""
            )
        }
        events: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in connection.execute(
            "SELECT id, client_order_id, event_type, broker_event_at FROM portfolio_execution_events ORDER BY broker_event_at, id"
        ):
            events[str(row["client_order_id"])].append(dict(row))
        return orders, sessions, events
    finally:
        connection.close()


def _visible_terminal(events: list[dict[str, object]], as_of: datetime) -> str:
    terminal = ""
    for event in events:
        event_at = _parse_time(event.get("broker_event_at"))
        status = str(event.get("event_type") or "").lower()
        if event_at is not None and event_at <= as_of and is_terminal_order_status(status):
            terminal = status
    return terminal


def _fill_outcome(actual: float | None, expected: float | None) -> str:
    if actual is None or actual <= 0:
        return "zero_fill"
    if expected is not None and actual >= expected - 1e-9:
        return "complete"
    return "partial"


def _calendar_anniversary(start: datetime, months: int) -> datetime:
    index = start.year * 12 + start.month - 1 + months
    year, month_index = divmod(index, 12)
    month = month_index + 1
    return start.replace(year=year, month=month, day=min(start.day, monthrange(year, month)[1]))


def _bootstrap_decision_ci(rows: list[dict[str, object]]) -> dict[str, object]:
    blocks: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = _number(row.get("paper_vs_zero_cost_open_bps"))
        if value is not None:
            blocks[str(row["decision_id"])].append(value)
    means = [statistics.fmean(values) for _, values in sorted(blocks.items()) if values]
    if not means:
        return {"unit": "decision", "decision_blocks": 0, "resamples": 0, "mean_ci_95": None}
    rng = random.Random(0)
    samples = sorted(statistics.fmean([rng.choice(means) for _ in means]) for _ in range(1000))
    return {
        "unit": "decision",
        "decision_blocks": len(means),
        "resamples": 1000,
        "mean_ci_95": [samples[24], samples[974]],
    }


def _market_report(rows: list[dict[str, object]], *, start: datetime, as_of: datetime) -> dict[str, object]:
    scoped = [
        row for row in rows if (at := _parse_time(row.get("decision_timestamp"))) is not None and start <= at <= as_of
    ]
    eligible = [row for row in scoped if bool(row.get("primary_price_eligible"))]
    excluded: Counter[str] = Counter()
    for row in scoped:
        reasons = row.get("primary_exclusion_reasons")
        if not bool(row.get("primary_price_eligible")) and isinstance(reasons, list):
            excluded.update(str(reason) for reason in reasons)
    actual = [_number(row.get("actual_price_cost")) for row in eligible]
    modeled = [_number(row.get("modeled_price_cost")) for row in eligible]
    return {
        "candidate_order_count": len(scoped),
        "eligible_decision_count": len({str(row["decision_id"]) for row in eligible}),
        "eligible_order_count": len(eligible),
        "excluded_count": len(scoped) - len(eligible),
        "excluded_count_by_reason": dict(sorted(excluded.items())),
        "raw_event_coverage": {
            "covered_orders": sum(int((_number(row.get("raw_event_count")) or 0) > 0) for row in scoped),
            "candidate_orders": len(scoped),
        },
        "valid_sequence_coverage": {
            "valid_orders": sum(bool(row.get("valid_cumulative_vwap")) for row in scoped),
            "candidate_orders": len(scoped),
        },
        "fee_completeness": {
            "event_local_observed_orders": sum(
                row.get("fee_evidence_status") == "observed_event_local" for row in scoped
            ),
            "unknown_orders": sum(row.get("fee_evidence_status") == "unknown" for row in scoped),
            "authoritative_reconciled_fee_activity_available": False,
            "total_cost_comparison_eligible": False,
        },
        "price_residual_bps": _summary(
            value for row in eligible if (value := _number(row.get("paper_vs_zero_cost_open_bps"))) is not None
        ),
        "decision_block_bootstrap": _bootstrap_decision_ci(eligible),
        "aggregate_price_cost": {
            "actual": sum(value for value in actual if value is not None),
            "modeled_3bps": sum(value for value in modeled if value is not None),
            "actual_minus_modeled": sum(value for value in actual if value is not None)
            - sum(value for value in modeled if value is not None),
        },
        "aggregate_actual_total_cost": None,
        "modeled_fees": sum(_number(row.get("modeled_fee")) or 0 for row in eligible),
        "total_cost_limitation": _FEE_LIMITATION,
    }


def _sample_gates(
    rows: list[dict[str, object]], as_of: datetime, minimums: Mapping[str, object]
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    origin_minimums, partial_minimums = minimums.get("origin_specific_claim"), minimums.get("partial_fill_model")
    if not isinstance(origin_minimums, Mapping) or not isinstance(partial_minimums, Mapping):
        raise ValueError("P1 manifest has malformed aggregate gates")
    for market in _MARKETS:
        required = minimums[market]
        assert isinstance(required, Mapping)
        market_rows = [
            row
            for row in rows
            if row["market"] == market
            and row["primary_price_eligible"]
            and (_parse_time(row["decision_timestamp"]) or as_of) <= as_of
        ]
        scheduled = [row for row in market_rows if row["decision_origin"] == "scheduled"]
        decisions = {str(row["decision_id"]): _parse_time(row["decision_timestamp"]) for row in scheduled}
        terminal = [row for row in market_rows if bool(row["is_terminal"])]
        decision_key = "independent_scheduled_decisions" if market == "equity" else "independent_96_hour_decisions"
        starts, months = [item for item in decisions.values() if item is not None], int(required["calendar_months"])
        observations = {
            "calendar_months": months if starts and as_of >= _calendar_anniversary(min(starts), months) else 0,
            decision_key: len(decisions),
            "terminal_orders": len(terminal),
            "buy_terminal_orders": sum(row["side"] == "buy" for row in terminal),
            "sell_terminal_orders": sum(row["side"] == "sell" for row in terminal),
        }
        gates = {
            "calendar_months": bool(starts and as_of >= _calendar_anniversary(min(starts), months)),
            decision_key: observations[decision_key] >= int(required[decision_key]),
            "terminal_orders": observations["terminal_orders"] >= int(required["terminal_orders"]),
            "buy_terminal_orders": observations["buy_terminal_orders"] >= int(required["buy_terminal_orders"]),
            "sell_terminal_orders": observations["sell_terminal_orders"] >= int(required["sell_terminal_orders"]),
        }
        origins: dict[str, dict[str, object]] = {}
        for origin in sorted({str(row["decision_origin"]) for row in market_rows}):
            origin_rows = [row for row in market_rows if str(row["decision_origin"]) == origin]
            origins[origin] = {
                "independent_decisions": len({str(row["decision_id"]) for row in origin_rows}),
                "orders": len(origin_rows),
                "minimum_met": len({str(row["decision_id"]) for row in origin_rows})
                >= int(origin_minimums["independent_decisions"])
                and len(origin_rows) >= int(origin_minimums["orders"]),
            }
        partial_events = sum(
            row["fill_outcome"] == "partial" or row["terminal_reason"] in {"rejected", "expired"} for row in market_rows
        )
        result[market] = {
            "minimums": dict(required),
            "observations": observations,
            "gates": gates,
            "all_minimums_met": all(gates.values()),
            "origin_specific_claims": origins,
            "partial_fill_modeling": {
                "partial_reject_or_expiry_events": partial_events,
                "eligible_orders": len(market_rows),
                "minimum_met": partial_events >= int(partial_minimums["partial_reject_or_expiry_events"])
                and len(market_rows) >= int(partial_minimums["eligible_orders"]),
            },
            "verdict": "UNKNOWN_COST_TRANSFERABILITY",
        }
    return result


def _receipt(
    contract: Mapping[str, object], dataset: Mapping[str, object], as_of: datetime, manifest_sha: str
) -> tuple[dict[str, object], datetime]:
    receipt = contract.get("epoch_receipt")
    if not isinstance(receipt, Mapping):
        raise ValueError("P1 contract requires an immutable epoch_receipt")
    required = {
        "manifest_commit",
        "manifest_sha256",
        "manifest_git_blob_sha",
        "manifest_commit_time",
        "deployed_code_commit",
        "deployment_timestamp",
        "sample_cutoff",
        "account_identity_hash",
        "configuration_digests",
        "first_eligible_session_ids",
        "settings_sha256",
        "local_bars_or_manifest_sha256",
        "feed_identity",
    }
    if missing := required - set(receipt):
        raise ValueError(f"P1 epoch receipt is missing: {', '.join(sorted(missing))}")
    deployment, cutoff, manifest_time = (
        _parse_time(receipt["deployment_timestamp"]),
        _parse_time(receipt["sample_cutoff"]),
        _parse_time(receipt["manifest_commit_time"]),
    )
    if not deployment or not cutoff or not manifest_time or not manifest_time <= deployment <= cutoff <= as_of:
        raise ValueError("P1 epoch receipt has invalid manifest/deployment/cutoff/as_of ordering")
    for key in ("configuration_digests", "first_eligible_session_ids"):
        if (
            not isinstance(receipt[key], Mapping)
            or set(receipt[key]) != set(_MARKETS)
            or not all(str(value) for value in receipt[key].values())
        ):
            raise ValueError(f"P1 epoch receipt has malformed {key}")
    if (
        str(receipt["manifest_sha256"]) != manifest_sha
        or str(receipt["local_bars_or_manifest_sha256"]) != str(dataset.get("sha256"))
        or str(receipt["feed_identity"]) != str(dataset.get("feed_identity"))
    ):
        raise ValueError("P1 epoch receipt does not match the local manifest or dataset identity")
    if len(str(receipt["account_identity_hash"])) != 64:
        raise ValueError("P1 epoch receipt has an invalid account identity hash")
    return dict(receipt), cutoff


def build_p1_report(
    db_path: str | Path,
    bars: Iterable[Any],
    config: Any,
    *,
    dataset: Mapping[str, object],
    contract: Mapping[str, object],
    as_of: datetime,
) -> dict[str, object]:
    """Apply the manifest-authoritative P1 epoch contract without DB writes."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.astimezone(UTC)
    manifest_path = contract.get("manifest_path")
    if manifest_path is None:
        path = _MANIFEST
    elif isinstance(manifest_path, str):
        path = Path(manifest_path)
    else:
        raise ValueError("P1 manifest_path must be a filesystem path")
    manifest, manifest_sha = _load_manifest(path)
    receipt, cutoff = _receipt(contract, dataset, as_of, manifest_sha)
    strategies = contract.get("strategy_identities")
    if not isinstance(strategies, Mapping) or set(strategies) != set(_MARKETS):
        raise ValueError("strategy_identities must declare equity and crypto")
    shadow = run_shadow_comparator(db_path, bars, config, dataset=dict(dataset), as_of=as_of)
    measurement = execution_measurement(db_path, as_of=as_of)
    lineage_by_order, sessions, events = _lineage(db_path)
    shadow_rows = shadow.get("orders")
    measurement_rows = measurement.get("orders")
    if not isinstance(shadow_rows, list) or not isinstance(measurement_rows, list):
        raise ValueError("P0 measurement output has an invalid order collection")
    shadow_by_order = {
        str(row["client_order_id"]): row for row in shadow_rows if isinstance(row, Mapping) and "client_order_id" in row
    }
    observed_by_order = {
        str(row["client_order_id"]): row
        for row in measurement_rows
        if isinstance(row, Mapping) and "client_order_id" in row
    }
    first_matches: dict[str, bool] = {}
    configuration_digests = receipt["configuration_digests"]
    first_session_ids = receipt["first_eligible_session_ids"]
    assert isinstance(configuration_digests, Mapping)
    assert isinstance(first_session_ids, Mapping)
    for market in _MARKETS:
        first = sessions.get(str(first_session_ids[market]))
        first_matches[market] = bool(
            first
            and str(first.get("mode", "")).lower() == "paper"
            and account_identity_hash(str(first.get("account_id", ""))) == receipt["account_identity_hash"]
            and str(first.get("configuration_digest", "")) == str(configuration_digests[market])
            and (_parse_time(first.get("started_at")) or datetime.min.replace(tzinfo=UTC)) >= cutoff
        )
    records: list[dict[str, object]] = []
    for order_id, observed in sorted(observed_by_order.items()):
        line, modeled = lineage_by_order.get(order_id, {}), shadow_by_order.get(order_id)
        market = str(observed.get("market") or line.get("market") or "unknown")
        decision_at, session = (
            _parse_time(line.get("decision_timestamp") or observed.get("decision_timestamp")),
            sessions.get(str(line.get("session_id") or ""), {}),
        )
        if decision_at is not None and decision_at > as_of:
            continue
        reasons: list[str] = []
        if market not in _MARKETS:
            reasons.append("unsupported_market")
        elif str(line.get("strategy_name", "")) != str(strategies[market]):
            reasons.append("strategy_identity_mismatch")
        if modeled is None or str(session.get("mode", "")).lower() != "paper":
            reasons.append("not_paper_or_no_shadow_schedule")
        if decision_at is None:
            reasons.append("invalid_decision_timestamp")
        elif decision_at < cutoff:
            reasons.append("pre_sample_cutoff")
        elif decision_at > as_of:
            reasons.append("after_as_of")
        session_at = _parse_time(session.get("started_at"))
        if session_at is None or session_at < cutoff:
            reasons.append("session_before_sample_cutoff")
        if market in _MARKETS:
            digest = str(configuration_digests[market])
            if (
                account_identity_hash(str(line.get("account_id", ""))) != receipt["account_identity_hash"]
                or account_identity_hash(str(session.get("account_id", ""))) != receipt["account_identity_hash"]
            ):
                reasons.append("account_identity_mismatch")
            if (
                str(line.get("configuration_digest", "")) != digest
                or str(session.get("configuration_digest", "")) != digest
            ):
                reasons.append("configuration_digest_mismatch")
            if not first_matches[market]:
                reasons.append("first_eligible_session_missing_or_mismatched")
            elif session_at is None or session_at < (
                _parse_time(sessions[str(first_session_ids[market])].get("started_at")) or cutoff
            ):
                reasons.append("session_precedes_first_eligible_session")
        raw_count = int(_number(observed.get("raw_event_count")) or 0)
        if raw_count == 0:
            reasons.append("legacy_or_missing_raw_execution_events")
        valid_vwap = bool(
            isinstance(observed.get("comparison_eligibility"), Mapping)
            and observed["comparison_eligibility"].get("price_only")
        )
        if not valid_vwap:
            reasons.append("invalid_cumulative_quantity_or_vwap_sequence")
        if modeled is None or _number(modeled.get("paper_vs_zero_cost_open_bps")) is None:
            reasons.append("no_comparable_zero_cost_dynamic_open_schedule")
        terminal_reason = _visible_terminal(events.get(order_id, []), as_of)
        actual, expected = (
            _number(modeled.get("actual_gross_quantity")) if modeled else None,
            _number(modeled.get("expected_quantity")) if modeled else None,
        )
        record: dict[str, object] = {
            "client_order_id": order_id,
            "decision_id": str(line.get("decision_id") or observed.get("decision_id") or ""),
            "decision_timestamp": decision_at.isoformat() if decision_at else None,
            "market": market,
            "symbol": str(observed.get("symbol") or ""),
            "side": str(observed.get("side") or ""),
            "status": terminal_reason or "open",
            "decision_origin": str(observed.get("decision_origin") or "scheduled"),
            "execution_role": str(observed.get("execution_role") or "immediate"),
            "raw_event_count": raw_count,
            "valid_cumulative_vwap": valid_vwap,
            "fee_evidence_status": "unknown"
            if str(observed.get("fee_status") or "unknown") == "unknown"
            else "observed_event_local",
            "dataset_feed_identity": str(dataset["feed_identity"]),
            "total_cost_comparison_eligible": False,
            "is_terminal": is_terminal_order_status(terminal_reason),
            "fill_outcome": _fill_outcome(actual, expected),
            "terminal_reason": terminal_reason or None,
            "primary_exclusion_reasons": sorted(set(reasons)),
            "primary_price_eligible": not reasons,
        }
        if modeled:
            record.update(
                {
                    key: modeled.get(key)
                    for key in (
                        "expected_quantity",
                        "actual_gross_quantity",
                        "actual_residual_quantity",
                        "scheduled_residual_quantity",
                        "decision_close_shortfall_bps",
                        "scheduled_zero_cost_open_vwap",
                        "paper_vwap",
                        "paper_vs_zero_cost_open_bps",
                        "actual_price_cost",
                        "modeled_price_cost",
                        "modeled_fee",
                        "lagged_adv",
                        "order_quantity_over_lagged_adv",
                        "measurement_quality",
                        "excluded_reasons",
                    )
                }
            )
        records.append(record)
    reports = {
        str(days): {
            market: _market_report(
                [row for row in records if row["market"] == market], start=as_of - timedelta(days=days), as_of=as_of
            )
            for market in _MARKETS
        }
        for days in _WINDOW_DAYS
    }
    minimums = manifest.get("minimum_sample_gates")
    assert isinstance(minimums, Mapping)
    return {
        "research_id": str(manifest["mission"]),
        "read_only": True,
        "network_or_broker_activity": False,
        "as_of": as_of.isoformat(),
        "sample_epoch_receipt": receipt,
        "manifest_sha256": manifest_sha,
        "records": records,
        "reporting_windows_days": reports,
        "sample_gate_progress": _sample_gates(records, as_of, minimums),
        "economic_verdict": "UNKNOWN_COST_TRANSFERABILITY",
        "promotion": "none",
        "fee_limitation": _FEE_LIMITATION,
    }
