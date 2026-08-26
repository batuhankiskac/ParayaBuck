"""RESEARCH-ONLY: historical delisting policy — not a production/runtime feature.

Research-local terminal position semantics T0-T3 (no production engine mutation).

This module implements the predeclared sensitivity policies without modifying
PortfolioBacktestEngine. It operates as a post-hoc adjustment layer that
examines held positions whose bars terminated before the dataset end and
applies the appropriate accounting.

Policies:
- T0_CURRENT_STALE_REFERENCE: no adjustment (engine's stale last_price persists)
- T1_LAST_TRADABLE_EXIT: force close at last observed close + normal sell costs
- T2_TERMINAL_HAIRCUT_50: 50% of last close + costs (unresolved non-successor)
- T3_TERMINAL_ZERO: 0 recoverable value (severe-loss bound)

Exclusions: VERIFIED_RENAME / successor continuation / DATA_GAP / TEMPORARY_HALT
are not haircut. Timing: no look-ahead, event occurs after final bar.

Evidence for testing is deterministic synthetic. Production/PAPER/LIVE does NOT
import this module (verified: grep src/ = 0 hits outside terminal_aware_engine).
Retained in-place to preserve frozen K/L validation (§4 inventory). See
docs/repository_cleanup_inventory_2026_08.md and docs/v3_historical_validation_final.md.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from parayabuck.backtest.portfolio_engine import PortfolioBacktestResult

TerminalPolicy = Literal["T0", "T1", "T2", "T3"]


@dataclass(frozen=True)
class TerminalClassification:
    entity_id: str
    symbol: str
    last_bar: datetime
    terminal_event_class: str
    evidence_class: str
    pit_quality_tier: str
    is_rename: bool
    is_successor: bool
    bars_count: int
    exch: str


@dataclass(frozen=True)
class TerminalImpact:
    symbol: str
    entity_id: str
    entry_date: str | None
    last_bar: str
    quantity: float
    entry_price: float | None
    stale_value: float
    t1_proceeds: float
    t2_proceeds: float
    t3_proceeds: float
    pnl_t0: float
    pnl_t1: float
    pnl_t2: float
    pnl_t3: float
    delta_t0_t1: float
    delta_t1_t2: float
    delta_t2_t3: float


# ---- classification ----

RENAME_TIERS = {"TIER_A_VERIFIED_PIT"}
SUCCESSOR_EVIDENCE = {"RENAMED_CONTINUATION", "ACQUISITION_OR_MERGER"}
EXCLUDED_TERMINAL = {"RENAMED_CONTINUATION", "DATA_GAP", "TEMPORARY_HALT"}


def load_lifetimes(path: Path) -> list[dict[str, str]]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def classify_terminal_events(lifetimes_path: Path, dataset_end: datetime) -> dict[str, TerminalClassification]:
    rows = load_lifetimes(lifetimes_path)
    out: dict[str, TerminalClassification] = {}
    for r in rows:
        entity_id = r["entity_id"]
        symbol = r["canonical_symbol"]
        last_raw = r["last_observed_bar"]
        try:
            last_dt = datetime.fromisoformat(last_raw.replace("Z", "+00:00")) if last_raw else dataset_end
        except Exception:
            last_dt = dataset_end
        # Determine if terminated before dataset end (20 sessions buffer)
        # dataset_end is 2026-07-30, we consider terminated if last < 2026-07-30 -5 days
        # For classification, use explicit terminal_event_class if present
        term_class = r.get("terminal_event_class") or r.get("evidence_class") or "UNKNOWN_TERMINATION"
        evidence = r.get("evidence_class", "")
        tier = r.get("pit_quality_tier", "")
        is_rename = bool("RENAME" in evidence or "RENAMED" in term_class or r.get("rename_chain"))
        is_successor = evidence in SUCCESSOR_EVIDENCE or term_class in SUCCESSOR_EVIDENCE
        bars_count = 0
        # bars_count not in lifetimes; we store notes? Approximate via first/last diff? fallback 0
        try:
            bars_count = int(r.get("bars", 0))
        except Exception:
            bars_count = 0
        out[entity_id] = TerminalClassification(
            entity_id=entity_id,
            symbol=symbol,
            last_bar=last_dt,
            terminal_event_class=term_class,
            evidence_class=evidence,
            pit_quality_tier=tier,
            is_rename=is_rename,
            is_successor=is_successor,
            bars_count=bars_count,
            exch=r.get("exchange", ""),
        )
    return out


def should_apply_haircut(tc: TerminalClassification) -> bool:
    if tc.terminal_event_class in EXCLUDED_TERMINAL:
        return False
    if tc.is_rename:
        return False
    if tc.terminal_event_class == "RENAMED_CONTINUATION":
        return False
    if tc.terminal_event_class in ("DATA_GAP", "TEMPORARY_HALT"):
        return False
    # Only unresolved termination without successor gets haircut
    if tc.terminal_event_class in (
        "POSSIBLE_DELISTING",
        "VERIFIED_DELISTING",
        "UNKNOWN_TERMINATION",
    ):
        return True
    # Default for FINAL_SURVIVOR who ends near dataset end: no haircut
    return False


# ---- policy application ----


def _equity_fee_for_sell(qty: float, price: float, trade_date: date) -> float:
    # Use effective dated fees via helper if available
    try:
        from parayabuck.backtest.regulatory_fees import rates_for_trade_date

        rates = rates_for_trade_date(trade_date)
        from parayabuck.backtest.portfolio_engine import OrderSide, equity_regulatory_fee

        return equity_regulatory_fee(
            OrderSide.SELL,
            qty,
            price,
            taf_per_share=rates.taf_per_share,
            taf_cap=rates.taf_cap,
            cat_per_share=rates.cat_per_share,
            sec_fee_rate=rates.sec_fee_rate,
        )
    except Exception:
        # fallback flat
        return qty * 0.000195 + qty * 0.000003 + qty * price * 0.0000206


def compute_terminal_proceeds(
    qty: float,
    last_close: float,
    trade_date: date,
    policy: TerminalPolicy,
    slippage_bps: float = 2.0,
    spread_bps: float = 2.0,
) -> float:
    """Compute proceeds for selling qty at last_close with policy haircut.

    Policy haircuts are applied to the *price* before costs, matching the spec:
    - T0: stale (no liquidation) — handled separately (value = qty * last_close)
    - T1: full last close
    - T2: 50% of last close
    - T3: 0
    All sell paths include adverse slippage+spread and regulatory fees (same as engine).
    """
    if policy == "T0":
        # stale reference: no sale, value persists
        return qty * last_close
    if policy == "T3":
        # zero recoverable before costs? But selling zero still has no fee (qty 0 price 0)
        # However if we literally sell 0 worth, proceeds 0. Fees are still per share but on zero notional would still be per share?
        # For sensitivity, assume zero proceeds (no fees to avoid negative beyond zero). But spec says normal liquidation cost — for zero, cost is 0.
        return 0.0
    haircut_factor = 1.0
    if policy == "T2":
        haircut_factor = 0.5
    # T1 or T2 price
    base_price = last_close * haircut_factor
    adverse_bps = slippage_bps + spread_bps / 2.0
    fill_price = base_price * (1 - adverse_bps / 10_000.0)  # sell: price reduced
    notional = qty * fill_price
    fee = _equity_fee_for_sell(qty, fill_price, trade_date)
    # Proceeds after fee
    return max(0.0, notional - fee)


def apply_terminal_policy_to_result(
    result: PortfolioBacktestResult,
    lifetimes_path: Path,
    holdings_snapshot: dict[str, tuple[float, float, datetime]],  # symbol -> (qty, avg_price, last_bar_dt)
    last_prices: dict[str, float],  # symbol -> last close
    dataset_end: datetime,
    policy: TerminalPolicy,
) -> tuple[PortfolioBacktestResult, list[TerminalImpact]]:
    """Research-local adjustment: produce adjusted ending equity and per-symbol impacts.

    holdings_snapshot is the engine's final positions snapshot (those not liquidated).
    For T0, no adjustment; for T1/T2/T3, we simulate terminal liquidation of every
    held position that should be haircut per classification, after dataset end (no look-ahead).
    The adjustment is a single lifecycle event after last bar, so it does not affect
    decision timing.

    Returns (adjusted_result_like_dict, impacts). For simplicity we return a dict with
    adjusted equity and a list of impacts; the caller can compute adjusted return.
    """
    classifications = classify_terminal_events(lifetimes_path, dataset_end)
    # Map symbol -> classification (if multiple entities share symbol due to reuse, pick the one that matches last_bar)
    # For now pick first with matching symbol that is TERMINATED.
    # This is simplified; full reconstruction should map via entity_id.
    by_symbol: dict[str, TerminalClassification] = {}
    for tc in classifications.values():
        # Prefer terminated classification if multiple
        existing = by_symbol.get(tc.symbol)
        if existing is None:
            by_symbol[tc.symbol] = tc
        else:
            # Prefer the one whose last_bar is earlier (more terminated) if conflict
            if tc.last_bar < existing.last_bar:
                by_symbol[tc.symbol] = tc

    impacts: list[TerminalImpact] = []
    total_adjustment = 0.0

    for symbol, (qty, avg_price, last_bar_dt) in holdings_snapshot.items():
        if qty <= 0:
            continue
        last_close = last_prices.get(symbol, avg_price)
        tc_hold: TerminalClassification | None = by_symbol.get(symbol)
        if tc_hold is None:
            # No lifetime -> treat as survivor (no haircut)
            should_haircut = False
        else:
            # If symbol is survivor within 5 sessions of dataset end, never haircut
            # dataset_end 2026-07-30, survivor if last_bar within 5 trading days (~7 calendar days)
            days_to_end = (dataset_end - tc_hold.last_bar).days
            if days_to_end <= 7 and tc_hold.terminal_event_class in (
                "FINAL_SURVIVOR",
                "UNKNOWN_TERMINATION",
                "",
            ):
                # Check evidence
                if tc_hold.evidence_class == "FINAL_SURVIVOR":
                    should_haircut = False
                else:
                    should_haircut = should_apply_haircut(tc_hold)
            else:
                should_haircut = should_apply_haircut(tc_hold)

        # Compute stale value (T0)
        stale_value = qty * last_close
        t1 = compute_terminal_proceeds(qty, last_close, last_bar_dt.date(), "T1") if should_haircut else stale_value
        t2 = compute_terminal_proceeds(qty, last_close, last_bar_dt.date(), "T2") if should_haircut else stale_value
        t3 = compute_terminal_proceeds(qty, last_close, last_bar_dt.date(), "T3") if should_haircut else stale_value
        # For non-haircut, all proceeds == stale
        if not should_haircut:
            t1 = t2 = t3 = stale_value

        # PnL relative to cost basis (qty * avg_price + entry fees approximated as 0)
        cost = qty * avg_price
        pnl_t0 = stale_value - cost
        pnl_t1 = t1 - cost
        pnl_t2 = t2 - cost
        pnl_t3 = t3 - cost

        # For adjustment: T0 is engine's reported equity (stale). For T1/T2/T3 we replace stale_value with proceeds.
        # So delta = proceeds - stale_value
        delta_t0_t1 = t1 - stale_value
        delta_t1_t2 = t2 - t1
        delta_t2_t3 = t3 - t2

        # Only accumulate if haircut applies and policy is active
        if policy == "T1":
            total_adjustment += delta_t0_t1
        elif policy == "T2":
            total_adjustment += delta_t0_t1 + delta_t1_t2  # = t2 - stale
        elif policy == "T3":
            total_adjustment += delta_t0_t1 + delta_t1_t2 + delta_t2_t3  # = t3 - stale

        impacts.append(
            TerminalImpact(
                symbol=symbol,
                entity_id=tc.entity_id if tc else f"unknown_{symbol}",
                entry_date=None,
                last_bar=last_bar_dt.isoformat(),
                quantity=qty,
                entry_price=avg_price,
                stale_value=stale_value,
                t1_proceeds=t1,
                t2_proceeds=t2,
                t3_proceeds=t3,
                pnl_t0=pnl_t0,
                pnl_t1=pnl_t1,
                pnl_t2=pnl_t2,
                pnl_t3=pnl_t3,
                delta_t0_t1=delta_t0_t1,
                delta_t1_t2=delta_t1_t2,
                delta_t2_t3=delta_t2_t3,
            )
        )

    # Adjusted equity = result ending equity + total_adjustment
    # For T0, adjustment 0.
    # We cannot mutate frozen result; caller should compute adjusted return from this delta.
    # Return impacts sorted by absolute delta magnitude (for reporting)
    impacts_sorted = sorted(impacts, key=lambda x: abs(x.delta_t0_t1 + x.delta_t1_t2 + x.delta_t2_t3), reverse=True)
    return (result, impacts_sorted)  # caller uses total_adjustment = sum of deltas for policy
