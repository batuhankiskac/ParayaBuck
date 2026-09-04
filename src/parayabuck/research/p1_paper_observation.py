"""P1 natural PAPER observation contract and read-only aggregation.

The P0 comparator remains the sole owner of the backtest-schedule mirror.
This module only applies the predeclared P1 sample epoch above that output.
It is deliberately unable to start a runtime, contact a provider, or write a
runtime database.
"""

from __future__ import annotations

import math
import random
import sqlite3
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from parayabuck.execution.shadow_comparator import run_shadow_comparator
from parayabuck.execution.telemetry import execution_measurement

_MARKETS = ("equity", "crypto")
_WINDOW_DAYS = (7, 30, 90)
_FEE_LIMITATION = "event-local fee evidence is not an authoritative reconciled fee activity"


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


def _lineage(path: str | Path) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    """Load only the identity fields needed to apply the sample epoch."""
    connection = _open_readonly(path)
    try:
        required = {"portfolio_runtime_sessions", "portfolio_decisions", "portfolio_orders"}
        available = {
            str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if not required.issubset(available):
            return {}, {}
        sessions = {
            str(row["session_id"]): dict(row)
            for row in connection.execute(
                """SELECT session_id, account_id, configuration_digest, mode, started_at
                   FROM portfolio_runtime_sessions"""
            ).fetchall()
        }
        orders = {
            str(row["client_order_id"]): dict(row)
            for row in connection.execute(
                """SELECT o.client_order_id, d.decision_id, d.session_id, d.account_id,
                          d.configuration_digest, d.decision_timestamp, d.market, d.strategy_name
                   FROM portfolio_orders o
                   JOIN portfolio_decisions d ON d.decision_id = o.decision_id"""
            ).fetchall()
        }
        return orders, sessions
    finally:
        connection.close()


def _notional_bucket(value: float | None) -> str:
    if value is None or value < 0:
        return "unknown"
    if value < 1_000:
        return "under_1k"
    if value < 10_000:
        return "1k_to_10k"
    if value < 100_000:
        return "10k_to_100k"
    return "100k_plus"


def _adv_bucket(value: float | None) -> str:
    if value is None or value < 0:
        return "unknown"
    if value < 0.01:
        return "under_1pct"
    if value < 0.05:
        return "1pct_to_5pct"
    if value < 0.10:
        return "5pct_to_10pct"
    return "10pct_plus"


def _terminal_outcome(record: Mapping[str, object]) -> str:
    status = str(record.get("status", "")).lower()
    if "reject" in status:
        return "rejected"
    if "expir" in status:
        return "expired"
    expected = _number(record.get("expected_quantity")) or 0.0
    actual = _number(record.get("actual_gross_quantity")) or 0.0
    if expected > 0 and actual >= expected - 1e-9 and status in {"filled", "fill"}:
        return "complete"
    if actual > 0 or "partial" in status:
        return "partial"
    return "open_or_unterminal"


def _bootstrap_decision_ci(rows: list[dict[str, object]]) -> dict[str, object]:
    """A deterministic decision-block bootstrap; orders are never resampled alone."""
    blocks: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = _number(row.get("paper_vs_zero_cost_open_bps"))
        if value is not None:
            blocks[str(row["decision_id"])].append(value)
    means = [statistics.fmean(values) for _, values in sorted(blocks.items()) if values]
    if not means:
        return {"unit": "decision", "decision_blocks": 0, "resamples": 0, "mean_ci_95": None}
    rng = random.Random(0)
    resamples = 1_000
    samples = [statistics.fmean([rng.choice(means) for _ in means]) for _ in range(resamples)]
    samples.sort()
    return {
        "unit": "decision",
        "decision_blocks": len(means),
        "resamples": resamples,
        "mean_ci_95": [samples[24], samples[974]],
    }


def _strata(rows: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    fields = (
        "market",
        "side",
        "symbol",
        "decision_origin",
        "execution_role",
        "terminal_outcome",
        "notional_bucket",
        "order_quantity_over_lagged_adv_bucket",
        "fee_evidence_status",
        "dataset_feed_identity",
    )
    return {field: dict(sorted(Counter(str(row.get(field, "unknown")) for row in rows).items())) for field in fields}


def _exclusion_reasons(record: Mapping[str, object]) -> tuple[str, ...]:
    value = record.get("primary_exclusion_reasons")
    if not isinstance(value, list):
        return ()
    return tuple(reason for reason in value if isinstance(reason, str))


def _market_report(rows: list[dict[str, object]], *, start: datetime | None = None) -> dict[str, object]:
    scoped = (
        rows
        if start is None
        else [
            row
            for row in rows
            if (timestamp := _parse_time(row.get("decision_timestamp"))) is not None and timestamp >= start
        ]
    )
    eligible = [row for row in scoped if bool(row.get("primary_price_eligible"))]
    excluded = Counter(
        reason for row in scoped if not bool(row.get("primary_price_eligible")) for reason in _exclusion_reasons(row)
    )
    raw_covered = sum(int((_number(row.get("raw_event_count")) or 0) > 0) for row in scoped)
    valid_sequence = sum(bool(row.get("valid_cumulative_vwap")) for row in scoped)
    event_local_observed = sum(row.get("fee_evidence_status") == "observed_event_local" for row in scoped)
    unknown_fees = sum(row.get("fee_evidence_status") == "unknown" for row in scoped)
    residuals = [_number(row.get("paper_vs_zero_cost_open_bps")) for row in eligible]
    actual_price = [_number(row.get("actual_price_cost")) for row in eligible]
    modeled_price = [_number(row.get("modeled_price_cost")) for row in eligible]
    return {
        "candidate_order_count": len(scoped),
        "eligible_decision_count": len({str(row["decision_id"]) for row in eligible}),
        "eligible_order_count": len(eligible),
        "excluded_count": len(scoped) - len(eligible),
        "excluded_count_by_reason": dict(sorted(excluded.items())),
        "raw_event_coverage": {"covered_orders": raw_covered, "candidate_orders": len(scoped)},
        "valid_sequence_coverage": {"valid_orders": valid_sequence, "candidate_orders": len(scoped)},
        "fee_completeness": {
            "event_local_observed_orders": event_local_observed,
            "unknown_orders": unknown_fees,
            "authoritative_reconciled_fee_activity_available": False,
            "total_cost_comparison_eligible": False,
        },
        "price_residual_bps": _summary(value for value in residuals if value is not None),
        "decision_block_bootstrap": _bootstrap_decision_ci(eligible),
        "aggregate_price_cost": {
            "actual": sum(value for value in actual_price if value is not None),
            "modeled_3bps": sum(value for value in modeled_price if value is not None),
            "actual_minus_modeled": sum(value for value in actual_price if value is not None)
            - sum(value for value in modeled_price if value is not None),
        },
        "aggregate_actual_total_cost": None,
        "modeled_fees": sum(_number(row.get("modeled_fee")) or 0.0 for row in eligible),
        "total_cost_limitation": _FEE_LIMITATION,
        "strata": _strata(eligible),
    }


def _sample_gates(rows: list[dict[str, object]], as_of: datetime) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    minimums: dict[str, dict[str, int]] = {
        "equity": {"decisions": 30, "orders": 150, "buy": 40, "sell": 40},
        "crypto": {"decisions": 45, "orders": 80, "buy": 25, "sell": 25},
    }
    for market, required in minimums.items():
        market_rows = [row for row in rows if row["market"] == market and row["primary_price_eligible"]]
        scheduled = [row for row in market_rows if row["decision_origin"] == "scheduled"]
        decisions = {str(row["decision_id"]): _parse_time(row["decision_timestamp"]) for row in scheduled}
        terminal = [
            row for row in market_rows if row["terminal_outcome"] in {"complete", "partial", "rejected", "expired"}
        ]
        starts = [item for item in decisions.values() if item is not None]
        elapsed_days = (as_of - min(starts)).days if starts else 0
        observations = {
            "calendar_months": elapsed_days / 30.4375,
            "independent_scheduled_decisions": len(decisions),
            "terminal_orders": len(terminal),
            "buy_terminal_orders": sum(row["side"] == "buy" for row in terminal),
            "sell_terminal_orders": sum(row["side"] == "sell" for row in terminal),
        }
        required_decisions = int(required["decisions"])
        required_orders = int(required["orders"])
        required_buy = int(required["buy"])
        required_sell = int(required["sell"])
        gates = {
            "six_calendar_months": observations["calendar_months"] >= 6,
            "independent_scheduled_decisions": observations["independent_scheduled_decisions"] >= required_decisions,
            "terminal_orders": observations["terminal_orders"] >= required_orders,
            "buy_terminal_orders": observations["buy_terminal_orders"] >= required_buy,
            "sell_terminal_orders": observations["sell_terminal_orders"] >= required_sell,
        }
        origins: dict[str, dict[str, object]] = {}
        for origin in sorted({str(row["decision_origin"]) for row in market_rows}):
            origin_rows = [row for row in market_rows if row["decision_origin"] == origin]
            origins[origin] = {
                "independent_decisions": len({str(row["decision_id"]) for row in origin_rows}),
                "orders": len(origin_rows),
                "minimum_met": len({str(row["decision_id"]) for row in origin_rows}) >= 20 and len(origin_rows) >= 30,
            }
        partial_events = sum(row["terminal_outcome"] in {"partial", "rejected", "expired"} for row in market_rows)
        result[market] = {
            "minimums": {"six_calendar_months": 6, **required},
            "observations": observations,
            "gates": gates,
            "all_minimums_met": all(gates.values()),
            "origin_specific_claims": origins,
            "partial_fill_modeling": {
                "partial_reject_or_expiry_events": partial_events,
                "eligible_orders": len(market_rows),
                "minimum_met": partial_events >= 30 and len(market_rows) >= 300,
            },
            "verdict": "UNKNOWN_COST_TRANSFERABILITY",
        }
    return result


def build_p1_report(
    db_path: str | Path,
    bars: Iterable[Any],
    config: Any,
    *,
    dataset: Mapping[str, object],
    contract: Mapping[str, object],
    as_of: datetime,
) -> dict[str, object]:
    """Apply the P1 epoch contract to P0 data without changing either input."""
    cutoff = _parse_time(contract.get("sample_cutoff"))
    if cutoff is None:
        raise ValueError("sample_cutoff must be an ISO-8601 timestamp")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    account = str(contract.get("account_identity") or "")
    digest = str(contract.get("configuration_digest") or "")
    first_session = str(contract.get("first_eligible_session_id") or "")
    strategies = contract.get("strategy_identities")
    feed = str(dataset.get("feed_identity") or "")
    if not account or not digest or not first_session or not isinstance(strategies, Mapping) or not feed:
        raise ValueError("P1 contract requires account, digest, session, strategy identities, and feed identity")
    if set(strategies) != set(_MARKETS):
        raise ValueError("strategy_identities must declare equity and crypto")

    shadow = run_shadow_comparator(db_path, bars, config, dataset=dict(dataset))
    measurement = execution_measurement(db_path)
    lineage_by_order, sessions = _lineage(db_path)
    shadow_rows = shadow.get("orders")
    measurement_rows = measurement.get("orders")
    if not isinstance(shadow_rows, list) or not isinstance(measurement_rows, list):
        raise ValueError("P0 measurement output has an invalid order collection")
    shadow_by_order = {
        str(row["client_order_id"]): row for row in shadow_rows if isinstance(row, Mapping) and "client_order_id" in row
    }
    first = sessions.get(first_session)
    first_start = _parse_time(first.get("started_at")) if first else None
    first_session_matches = bool(
        first
        and str(first.get("account_id")) == account
        and str(first.get("configuration_digest")) == digest
        and str(first.get("mode", "")).lower() == "paper"
        and first_start is not None
    )

    records: list[dict[str, object]] = []
    for observed in measurement_rows:
        if not isinstance(observed, Mapping):
            continue
        order_id = str(observed.get("client_order_id"))
        line = lineage_by_order.get(order_id, {})
        modeled = shadow_by_order.get(order_id)
        decision_at = _parse_time(line.get("decision_timestamp") or observed.get("decision_timestamp"))
        session = sessions.get(str(line.get("session_id") or ""), {})
        reasons: list[str] = []
        if modeled is None or str(session.get("mode", "")).lower() != "paper":
            reasons.append("not_paper_or_no_shadow_schedule")
        if decision_at is None:
            reasons.append("invalid_decision_timestamp")
        elif decision_at < cutoff:
            reasons.append("pre_sample_cutoff")
        session_started_at = _parse_time(session.get("started_at"))
        if session_started_at is None or session_started_at < cutoff:
            reasons.append("session_before_sample_cutoff")
        if str(line.get("account_id", "")) != account or str(session.get("account_id", "")) != account:
            reasons.append("account_identity_mismatch")
        if (
            str(line.get("configuration_digest", "")) != digest
            or str(session.get("configuration_digest", "")) != digest
        ):
            reasons.append("configuration_digest_mismatch")
        if not first_session_matches:
            reasons.append("first_eligible_session_missing_or_mismatched")
        elif first_start is not None and (session_started_at or datetime.min.replace(tzinfo=UTC)) < first_start:
            reasons.append("session_precedes_first_eligible_session")
        market = str(observed.get("market") or line.get("market") or "unknown")
        if market not in _MARKETS:
            reasons.append("unsupported_market")
        elif str(line.get("strategy_name", "")) != str(strategies[market]):
            reasons.append("strategy_identity_mismatch")
        raw_count = int(_number(observed.get("raw_event_count")) or 0)
        if raw_count == 0:
            reasons.append("legacy_or_missing_raw_execution_events")
        eligibility = observed.get("comparison_eligibility")
        valid_vwap = isinstance(eligibility, Mapping) and bool(eligibility.get("price_only"))
        if not valid_vwap:
            reasons.append("invalid_cumulative_quantity_or_vwap_sequence")
        if modeled is None or _number(modeled.get("paper_vs_zero_cost_open_bps")) is None:
            reasons.append("no_comparable_zero_cost_dynamic_open_schedule")
        fee_status = str(observed.get("fee_status") or "unknown")
        record: dict[str, object] = {
            "client_order_id": order_id,
            "decision_id": str(line.get("decision_id") or observed.get("decision_id") or ""),
            "decision_timestamp": decision_at.isoformat() if decision_at else None,
            "market": market,
            "symbol": str(observed.get("symbol") or ""),
            "side": str(observed.get("side") or ""),
            "status": str(observed.get("status") or ""),
            "decision_origin": str(observed.get("decision_origin") or "scheduled"),
            "execution_role": str(observed.get("execution_role") or "immediate"),
            "raw_event_count": raw_count,
            "valid_cumulative_vwap": valid_vwap,
            "fee_evidence_status": "unknown" if fee_status == "unknown" else "observed_event_local",
            "dataset_feed_identity": feed,
            "total_cost_comparison_eligible": False,
            "primary_exclusion_reasons": sorted(set(reasons)),
            "primary_price_eligible": not reasons,
        }
        if modeled is not None:
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
            expected_notional = _number(modeled.get("expected_notional"))
        else:
            expected_notional = None
        record["terminal_outcome"] = _terminal_outcome(record)
        record["notional_bucket"] = _notional_bucket(expected_notional)
        record["order_quantity_over_lagged_adv_bucket"] = _adv_bucket(
            _number(record.get("order_quantity_over_lagged_adv"))
        )
        records.append(record)

    reports: dict[str, dict[str, object]] = {}
    for days in _WINDOW_DAYS:
        start = as_of - timedelta(days=days)
        reports[str(days)] = {
            market: _market_report([row for row in records if row["market"] == market], start=start)
            for market in _MARKETS
        }
    return {
        "research_id": "execution_cost_transferability_p1_paper_observation_2026_09",
        "read_only": True,
        "network_or_broker_activity": False,
        "as_of": as_of.astimezone(UTC).isoformat(),
        "sample_epoch_contract": {
            "sample_cutoff": cutoff.isoformat(),
            "account_identity_matched": first_session_matches,
            "configuration_digest": digest,
            "first_eligible_session_id": first_session,
            "strategy_identities": dict(strategies),
            "dataset": dict(dataset),
        },
        "records": records,
        "reporting_windows_days": reports,
        "sample_gate_progress": _sample_gates(records, as_of.astimezone(UTC)),
        "economic_verdict": "UNKNOWN_COST_TRANSFERABILITY",
        "promotion": "none",
        "fee_limitation": _FEE_LIMITATION,
    }
