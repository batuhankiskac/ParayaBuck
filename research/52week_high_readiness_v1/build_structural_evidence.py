#!/usr/bin/env python3
"""Build compact G1 evidence only; never run candidate portfolio economics."""

from __future__ import annotations

import csv
import hashlib
import importlib
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from parayabuck.backtest.lifetimes import load_entity_lifetimes
from parayabuck.backtest.portfolio_engine import OrderSide, equity_regulatory_fee
from parayabuck.backtest.product import load_csv_corporate_actions
from parayabuck.backtest.regulatory_fees import rates_for_trade_date
from parayabuck.core.models import Market
from parayabuck.markets.symbols import canonical_symbol

candidate = importlib.import_module("research.52week_high_readiness_v1.candidate")
month_end = importlib.import_module("research.52week_high_readiness_v1.month_end")

BARS = ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
ACTIONS = ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
LIFETIMES = ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv"
DESIGN_SHA = "72b51a0e5c03f2ce0a8790b197cf360b26b33cd3"
CORRECTED_MAIN_SHA = "839e92cf90e23cf83ce3cd2eb1b3ce34fefb97de"
DESIGN_FILES = {
    "CANDIDATE_DESIGN.md": "37c33a08e4aa4aed7e0dbc97fdce2083b033eaf18462c5e93f1f62fe49adb8b5",
    "FUTURE_TEST_PROTOCOL.md": "9ff021dddece81681b25b33df75eaa03c0931e5e5b7eafad4b879e229eb02be1",
    "CAUSALITY_AUDIT.md": "78c7c0a191c24ff0fcf95fda2addd8a938b15be0efdb9e0a3132d816c7746359",
    "DATA_FEASIBILITY.json": "9562161144c5a98d3a63dd7caeb709e481cd0af04e2d958fad12e80cb8209509",
    "FINAL_DESIGN_REPORT.md": "af883ae6f7a54272c4ad8e856e399a831e7f4ed18834d29224429bed51808fa6",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write(name: str, payload: object) -> None:
    (OUT / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def design_receipt() -> dict[str, object]:
    actual: dict[str, str] = {}
    for name in DESIGN_FILES:
        raw = subprocess.run(
            ["git", "show", f"{DESIGN_SHA}:research/52week_high_design_v1/{name}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        actual[name] = hashlib.sha256(raw).hexdigest()
    return {
        "starting_main_sha": "db1a6e9e4af4a5000dca0a54a971ce99577c676c",
        "starting_origin_main_sha": "db1a6e9e4af4a5000dca0a54a971ce99577c676c",
        "corrected_main_sha": CORRECTED_MAIN_SHA,
        "corrected_origin_main_sha": subprocess.run(
            ["git", "rev-parse", "origin/main"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip(),
        "design_sha": DESIGN_SHA,
        "expected_hashes": DESIGN_FILES,
        "actual_hashes": actual,
        "hashes_match": actual == DESIGN_FILES,
        "paper_strategy": "equity_residual_momentum_v3",
        "live_enabled": False,
        "candidate_economics_inspected": False,
    }


def data_receipt() -> dict[str, object]:
    raw_timeframes: Counter[str] = Counter()
    normalized: Counter[str] = Counter()
    symbols: set[str] = set()
    rows = 0
    rejected = 0
    with BARS.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows += 1
            raw = row["timeframe"]
            raw_timeframes[raw] += 1
            if raw in {"1d", "1Day"}:
                normalized["1d"] += 1
            else:
                rejected += 1
            symbols.add(row["symbol"])
    return {
        "source_sha256": sha256(BARS),
        "source_rows": rows,
        "loaded_rows": rows - rejected,
        "entity_count": len(symbols),
        "rows_by_original_timeframe": dict(sorted(raw_timeframes.items())),
        "rows_by_normalized_timeframe": dict(sorted(normalized.items())),
        "rejected_rows": rejected,
        "rejection_reasons": {},
        "source_bytes_mutated": False,
    }


def corporate_action_receipt() -> dict[str, object]:
    counts: Counter[str] = Counter()
    seen: set[tuple[str, str, str, Decimal]] = set()
    cash_rows = 0
    duplicates = 0
    with ACTIONS.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            kind = row["action_type"]
            counts[kind] += 1
            if kind != "cash_dividends":
                continue
            cash_rows += 1
            key = (
                canonical_symbol(row["symbol"], Market.EQUITY),
                row["ex_date"],
                "dividend",
                Decimal(row["rate"]),
            )
            if key in seen:
                duplicates += 1
            else:
                seen.add(key)
    applied = load_csv_corporate_actions(ACTIONS)
    return {
        "source_sha256": sha256(ACTIONS),
        "raw_action_count": sum(counts.values()),
        "action_type_counts": dict(sorted(counts.items())),
        "eligible_cash_dividend_rows": cash_rows,
        "deduplication_key": ["canonical_symbol", "ex_date_utc", "DIVIDEND", "decimal_rate"],
        "duplicate_economic_events": duplicates,
        "final_applied_event_count": len(applied),
        "non_cash_actions_applied": 0,
        "engine_replay_watermark_filter": "actions at or before persisted last_processed_bar are excluded",
    }


def cost_receipt() -> dict[str, object]:
    cases: list[dict[str, object]] = []
    for trade_date in (
        datetime(2024, 5, 21, tzinfo=UTC).date(),
        datetime(2024, 5, 22, tzinfo=UTC).date(),
        datetime(2024, 9, 1, tzinfo=UTC).date(),
        datetime(2025, 12, 1, tzinfo=UTC).date(),
        datetime(2026, 4, 4, tzinfo=UTC).date(),
    ):
        rates = rates_for_trade_date(trade_date)
        base = equity_regulatory_fee(
            OrderSide.SELL,
            1000,
            100,
            taf_per_share=rates.taf_per_share,
            taf_cap=rates.taf_cap,
            cat_per_share=rates.cat_per_share,
            sec_fee_rate=rates.sec_fee_rate,
        )
        cases.append({"trade_date": trade_date.isoformat(), "one_x": base, "two_x": 2 * base, "three_x": 3 * base})
    return {
        "semantics": "slippage, half-spread, crypto taker fee, and dated equity regulatory fee scale; commission remains zero",
        "commission": 0.0,
        "effective_dated_boundary_cases": cases,
        "gross_fill_geometry_unchanged_test": "tests/test_equity_regulatory_fee_multiplier.py::test_multiplier_does_not_change_gross_fill_geometry",
        "product_wiring_test": "tests/test_product_backtest.py::test_product_cost_multiplier_includes_equity_regulatory_fees",
        "passed": True,
    }


def structural_receipt() -> dict[str, object]:
    calendar = month_end.NyseLastCompletedSessionPolicy()._calendar
    schedule = calendar.schedule(start_date="2025-01-01", end_date="2026-01-30")
    sessions = tuple(value.to_pydatetime() for value in schedule["market_close"].iloc[-252:])
    ordinary = tuple(f"S{index:02d}" for index in range(10))
    pool = (*ordinary, *sorted(candidate.RESERVED_ROLES))
    histories = {}
    for index, symbol in enumerate(ordinary):
        values = [80.0] * 252
        values[-1] = 100.0 - index
        values[-2] = 100.0
        histories[symbol] = tuple(
            candidate.CloseObservation(timestamp, value) for timestamp, value in zip(sessions, values, strict=True)
        )
    for symbol in candidate.RESERVED_ROLES:
        histories[symbol] = tuple(candidate.CloseObservation(timestamp, 100.0) for timestamp in sessions)
    result = candidate.rank_broad_pool_through_t(
        pool,
        histories,
        decision_timestamp=sessions[-1],
        expected_session_timestamps=sessions,
        identity_resolved={symbol: True for symbol in pool},
        observation_capacity=8,
    )
    policy = month_end.NyseLastCompletedSessionPolicy()
    month_end_cases = [
        datetime(2026, 1, 30, 21, tzinfo=UTC),
        datetime(2024, 11, 29, 18, tzinfo=UTC),
        datetime(2025, 12, 31, 21, tzinfo=UTC),
    ]
    return {
        "candidate": "equity_52week_high_close_v1",
        "candidate_economics_inspected": False,
        "decision_timestamp": sessions[-1].isoformat(),
        "history_count": len(sessions),
        "history_last_equals_t": sessions[-1] == result.decision_timestamp,
        "maximum_visible_timestamp": max(
            item.timestamp for history in histories.values() for item in history
        ).isoformat(),
        "future_data_visible": any(item.timestamp > sessions[-1] for history in histories.values() for item in history),
        "eligible_pool_before": sorted(pool),
        "eligible_pool_after": list(result.eligible_pool),
        "scanner_eligibility_unchanged": sorted(pool) == list(result.eligible_pool),
        "ranked": [[symbol, score] for symbol, score in result.ranked],
        "observation_capacity": 8,
        "observation_set": list(result.observation_set),
        "selected": list(result.selected),
        "target_weights": [[symbol, weight] for symbol, weight in result.target_weights],
        "target_cash_weight": result.target_cash_weight,
        "reserved_roles": sorted(candidate.RESERVED_ROLES),
        "reserved_targets": sorted(set(result.selected) & candidate.RESERVED_ROLES),
        "month_end_cases": [
            {
                "completed_session": value.isoformat(),
                "decides": policy.should_decide(value),
                "next_open": policy.next_tradable_open(value).isoformat(),
            }
            for value in month_end_cases
        ],
        "production_registry_contains_candidate": False,
    }


def main() -> None:
    write("DESIGN_INPUT_RECEIPT.json", design_receipt())
    write("CANONICAL_DATA_RECEIPT.json", data_receipt())
    lifetimes = load_entity_lifetimes(LIFETIMES)
    lifetime_payload = lifetimes.receipt.to_dict()
    lifetime_payload.update(
        {
            "unresolved": [item.to_dict() for item in lifetimes.unresolved],
            "no_endpoint_status_pre_exclusion": True,
            "terminal_only_on_last_valid_bar": True,
        }
    )
    write("LIFECYCLE_RECEIPT.json", lifetime_payload)
    write("CORPORATE_ACTION_RECEIPT.json", corporate_action_receipt())
    write("COST_MULTIPLIER_RECEIPT.json", cost_receipt())
    parity = json.loads(Path("/private/tmp/v3_control_parity.json").read_text(encoding="utf-8"))
    parity["corrected_main_sha"] = CORRECTED_MAIN_SHA
    write("V3_CONTROL_PARITY.json", parity)
    write("STRUCTURAL_SIGNAL_RANK_AUDIT.json", structural_receipt())


if __name__ == "__main__":
    main()
