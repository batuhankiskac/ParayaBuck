#!/usr/bin/env python3
"""Current-parity, research-only equity profit-protection experiment.

This module is deliberately a sibling of the retired 2026-09 runner.  It
uses the current V5 loader, PIT universe preparation, numeric history cache,
effective-dated fee model, next-open execution engine, and T1 terminal
lifecycle.  It never registers a strategy and is never imported by
``src/parayabuck``.

The experiment has two hard integrity gates before candidate economics:

* the three required V5 files must match their frozen byte hashes;
* E0 must reproduce the current-parity control twice, including target,
  fill, equity-curve, terminal-event, and universe component hashes.

The candidate state machine is research-owned.  The production engine remains
the authority for quantities, weighted bases, fills, corporate-action
accounting, and terminal events; this module only observes those authoritative
events and records its own episode state beside the normal PortfolioState.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import research_v3_equity_roadmap as roadmap  # noqa: E402
from research_v3_current_parity_control import (  # noqa: E402
    _result_components as parity_result_components,
)

from parayabuck.backtest.portfolio_engine import (  # noqa: E402
    OrderSide,
    PortfolioBacktestResult,
    PortfolioFill,
)
from parayabuck.backtest.terminal_aware_engine import TerminalAwareResearchEngine, TerminalInfo  # noqa: E402
from parayabuck.config import Config, load_config  # noqa: E402
from parayabuck.core.models import Bar  # noqa: E402
from parayabuck.data.market_dataset import CorporateAction, CorporateActionType  # noqa: E402
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config  # noqa: E402
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy  # noqa: E402
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight  # noqa: E402
from parayabuck.utils.io import atomic_write  # noqa: E402

MISSION = "equity_profit_protection_2026_09_r2"
MANIFEST_PATH = REPO / "data/research_manifests" / f"{MISSION}.json"
DEFAULT_OUTPUT_DIR = REPO / "data/research" / MISSION
DEFAULT_REFERENCE_RECEIPT = Path("/private/tmp/equity_profit_protection_current_control.json")

DATASET_PATHS: dict[str, Path] = {
    "bars": roadmap.DATASET_V5,
    "lifetimes": roadmap.LIFETIMES_V5,
    "corporate_actions": roadmap.CA_V5,
}
EXPECTED_DATASET_HASHES = {
    "bars": "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139",
    "lifetimes": "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596",
    "corporate_actions": "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23",
}

CANDIDATES = ("E0_CONTROL", "E1_TP5_CLOSE", "E2_PROFIT_LOCK_50", "E3_PROFIT_ALPHA_CONFIRMED")
PROTECTION_CANDIDATES = CANDIDATES[1:]
FROZEN_PARAMETERS = dict(roadmap.FROZEN_PARAMETERS)
REQUIRED_LEDGER_FIELDS = (
    "episode_id",
    "symbol",
    "entry_time",
    "managed_weighted_basis",
    "activation_time",
    "activation_price",
    "high_close",
    "floor_price",
    "alpha_evidence",
    "trigger_time",
    "trigger_close",
    "intended_reason",
    "actual_fill_time",
    "actual_fill_price",
    "trigger_fill_gap",
    "quantity_initial",
    "quantity_exited",
    "partial_fill_count",
    "remaining_quantity",
    "control_exit_time",
    "control_exit_price",
    "peak_open_profit",
    "peak_to_exit_giveback",
    "mae",
    "mfe",
    "missed_upside",
    "avoided_loss_or_giveback",
    "incremental_costs",
    "net_protection_value",
    "same_symbol_reentry_time",
    "same_symbol_reentry_price",
    "same_symbol_reentry_pnl",
    "contemporaneous_account_equity_impact",
)

STAGE_ONE_GATE_NAMES = (
    "control_parity",
    "complete_run_no_incomplete_batches",
    "full_sharpe_not_below_control",
    "full_return_floor",
    "max_drawdown_not_materially_worse",
    "net_protection_value_positive",
    "recent_sharpe_not_below_control",
    "no_intermediate_exposure_increase",
    "no_intra_week_buy_or_replacement",
    "strict_observation_idempotency",
    "complete_exit_ledger",
)


class ResearchBlocked(RuntimeError):
    """A fail-closed research gate with a machine-readable blocker token."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_required_data() -> dict[str, dict[str, object]]:
    """Verify all required inputs before loading any economic data."""
    identity: dict[str, dict[str, object]] = {}
    for name, path in DATASET_PATHS.items():
        expected = EXPECTED_DATASET_HASHES[name]
        if not path.exists():
            raise ResearchBlocked("BLOCKED_DATA_MISSING_OR_HASH_MISMATCH", f"missing {path}")
        actual = _sha256(path)
        if actual != expected:
            raise ResearchBlocked(
                "BLOCKED_DATA_MISSING_OR_HASH_MISMATCH",
                f"{path}: expected {expected}, got {actual}",
            )
        identity[name] = {
            "path": str(path.relative_to(REPO)),
            "sha256": actual,
            "bytes": path.stat().st_size,
        }
    return identity


def corporate_action_source_audit(path: Path = roadmap.CA_V5) -> dict[str, object]:
    """Audit every CA row without double-applying split-adjusted prices.

    V5 executable bars are already split-adjusted.  The canonical current
    loader therefore applies cash dividends and audits stock/split rows as
    source completeness evidence; re-applying those split rows would count
    corporate actions twice.  Synthetic engine tests still exercise actual
    split quantity/basis handling.
    """
    counts: Counter[str] = Counter()
    symbols: set[str] = set()
    rows = 0
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows += 1
            action_type = str(row.get("action_type", ""))
            counts[action_type] += 1
            if row.get("symbol"):
                symbols.add(str(row["symbol"]))
    return {
        "rows": rows,
        "symbols": len(symbols),
        "action_type_counts": dict(sorted(counts.items())),
        "applied_by_current_parity_loader": ["cash_dividends"],
        "split_rows_not_reapplied_reason": "executable V5 bars are split-adjusted",
    }


def _timestamp(value: datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _iso(value: datetime | str | None) -> str | None:
    parsed = _timestamp(value)
    return parsed.isoformat() if parsed is not None else None


@dataclass
class EpisodeState:
    """Research-only state for one managed position episode.

    ``quantity`` and ``basis`` remain the authoritative snapshot mirrored from
    the engine.  Protection state is never inferred from a raw quantity delta:
    explicit BUY fills, SELL fills, split actions, and terminal events drive
    the transitions.
    """

    quantity: float
    basis: float
    episode_id: str = ""
    entry_time: str | None = None
    armed: bool = False
    high_close: float | None = None
    floor_price: float | None = None
    breakdown_count: int = 0
    pending: bool = False
    activation_time: str | None = None
    activation_price: float | None = None
    last_observed_timestamp: str | None = None
    last_breakdown_timestamp: str | None = None
    trigger_timestamp: str | None = None
    trigger_close: float | None = None
    intended_reason: str | None = None
    alpha_evidence: dict[str, object] = field(default_factory=dict)

    def apply_buy(self, qty: float, price: float, timestamp: datetime | str | None = None) -> None:
        """Apply an explicit BUY fill and reset protection on net increase."""
        if qty <= 0 or price <= 0:
            raise ValueError("BUY fill must have positive quantity and price")
        ts = _iso(timestamp)
        if self.quantity <= 1e-12:
            self.quantity = qty
            self.basis = price
            if ts is not None:
                self.entry_time = ts
            self.armed = False
            self.high_close = None
            self.floor_price = None
            self.breakdown_count = 0
            self.pending = False
            self.activation_time = None
            self.activation_price = None
            self.last_observed_timestamp = None
            self.last_breakdown_timestamp = None
            self.trigger_timestamp = None
            self.trigger_close = None
            self.intended_reason = None
            self.alpha_evidence = {}
            return
        total = self.quantity + qty
        self.basis = (self.quantity * self.basis + qty * price) / total
        self.quantity = total
        # Additional BUY is a deliberate protection reset, but it remains the
        # same episode and preserves the original weighted entry time.
        self.armed = False
        self.high_close = None
        self.floor_price = None
        self.breakdown_count = 0
        self.pending = False
        self.activation_time = None
        self.activation_price = None
        self.last_breakdown_timestamp = None
        self.trigger_timestamp = None
        self.trigger_close = None
        self.intended_reason = None
        self.alpha_evidence = {}

    def apply_sell(self, qty: float) -> None:
        """Apply an authoritative SELL fill; partial trims preserve state."""
        if qty < 0:
            raise ValueError("SELL quantity cannot be negative")
        self.quantity = max(0.0, self.quantity - qty)
        if self.quantity <= 1e-12:
            self.quantity = 0.0
            self.armed = False
            self.high_close = None
            self.floor_price = None
            self.breakdown_count = 0
            self.pending = False
            self.activation_time = None
            self.activation_price = None
            self.last_observed_timestamp = None
            self.last_breakdown_timestamp = None

    def apply_split(self, ratio: float) -> None:
        """Adjust quantity and every per-share protection level atomically."""
        if ratio <= 0:
            raise ValueError("Split ratio must be positive")
        self.quantity *= ratio
        self.basis /= ratio
        for attr in ("high_close", "floor_price", "activation_price", "trigger_close"):
            value = getattr(self, attr)
            if value is not None:
                setattr(self, attr, value / ratio)
        # Armed/pending/activation timestamps intentionally survive a split.

    def observe(
        self,
        close: float,
        candidate: str,
        *,
        qualified: bool = True,
        rank: int = 1,
        timestamp: datetime | str | None = None,
    ) -> bool:
        """Consume one completed close, with strict timestamp idempotency."""
        if close <= 0 or self.quantity <= 0:
            return False
        ts = _timestamp(timestamp)
        if ts is not None:
            previous = _timestamp(self.last_observed_timestamp)
            if previous is not None and ts <= previous:
                return False
            self.last_observed_timestamp = ts.isoformat()
        if self.pending:
            return False
        gain = close / self.basis - 1.0 if self.basis > 0 else float("-inf")
        if candidate == "E1_TP5_CLOSE":
            if gain >= 0.05:
                self.pending = True
                self.trigger_timestamp = _iso(timestamp)
                self.trigger_close = close
                self.intended_reason = "take_profit_5pct_close"
                self.alpha_evidence = {"qualified": qualified, "rank": rank}
            return self.pending
        if candidate not in {"E2_PROFIT_LOCK_50", "E3_PROFIT_ALPHA_CONFIRMED"}:
            raise ValueError(f"Unsupported protection candidate: {candidate}")
        if not self.armed:
            if gain >= 0.05:
                self.armed = True
                self.high_close = close
                self.floor_price = self.basis + 0.5 * (close - self.basis)
                self.activation_time = _iso(timestamp)
                self.activation_price = close
            # The activation close is observation-only; it cannot also exit.
            return False
        self.high_close = max(float(self.high_close or close), close)
        self.floor_price = max(self.basis, self.basis + 0.5 * (self.high_close - self.basis))
        if candidate == "E2_PROFIT_LOCK_50" and close <= self.floor_price:
            self.pending = True
            self.trigger_timestamp = _iso(timestamp)
            self.trigger_close = close
            self.intended_reason = "profit_lock_50pct_close"
            self.alpha_evidence = {"qualified": qualified, "rank": rank}
        elif candidate == "E3_PROFIT_ALPHA_CONFIRMED":
            bad_alpha = (not qualified) or rank > 12
            if bad_alpha:
                self.breakdown_count += 1
                self.last_breakdown_timestamp = _iso(timestamp)
            else:
                self.breakdown_count = 0
                self.last_breakdown_timestamp = None
            if self.breakdown_count >= 2:
                self.pending = True
                self.trigger_timestamp = _iso(timestamp)
                self.trigger_close = close
                self.intended_reason = "profit_alpha_confirmed_two_closes"
                self.alpha_evidence = {"qualified": qualified, "rank": rank, "confirmation": 2}
        return self.pending

    def snapshot(self) -> dict[str, object]:
        return dataclasses.asdict(self)

    @classmethod
    def restore(cls, raw: Mapping[str, object]) -> EpisodeState:
        allowed = {field.name for field in dataclasses.fields(cls)}
        values = {key: value for key, value in raw.items() if key in allowed}
        values["quantity"] = float(values.get("quantity", 0.0))
        values["basis"] = float(values.get("basis", 0.0))
        values["breakdown_count"] = int(values.get("breakdown_count", 0))
        values["alpha_evidence"] = dict(values.get("alpha_evidence", {}))
        return cls(**values)  # type: ignore[arg-type]


def _new_episode_record(state: EpisodeState, symbol: str) -> dict[str, object]:
    record = {field: None for field in REQUIRED_LEDGER_FIELDS}
    record.update(
        {
            "episode_id": state.episode_id,
            "symbol": symbol,
            "entry_time": state.entry_time,
            "managed_weighted_basis": state.basis,
            "quantity_initial": state.quantity,
            "quantity_exited": 0.0,
            "partial_fill_count": 0,
            "remaining_quantity": state.quantity,
            "alpha_evidence": {},
            "peak_open_profit": 0.0,
            "peak_to_exit_giveback": 0.0,
            "mae": 0.0,
            "mfe": 0.0,
            "avoided_loss_or_giveback": 0.0,
            "incremental_costs": 0.0,
            "net_protection_value": 0.0,
        }
    )
    return record


class ProfitProtectionStrategy(roadmap.RoadmapControl):
    """Frozen V3 entries plus a candidate-owned close observer."""

    name = "equity_residual_momentum_v3_profit_protection_r2"
    version = "1.0.0"
    display_name = "V3 profit protection research R2"
    rebalance_policy_declared = "weekly"
    cadence_label = "weekly"

    def __init__(
        self,
        candidate: str,
        *,
        state_snapshot: Mapping[str, object] | None = None,
    ) -> None:
        if candidate not in PROTECTION_CANDIDATES:
            raise ValueError(f"Unsupported protection candidate: {candidate}")
        super().__init__(dict(FROZEN_PARAMETERS))
        self.candidate = candidate
        self.states: dict[str, EpisodeState] = {}
        self.managed_symbols: set[str] = set()
        self.exit_ledger: list[dict[str, object]] = []
        self._open_records: dict[str, dict[str, object]] = {}
        self._pending_triggers: set[str] = set()
        self._next_episode_number = 1
        self._decision_is_scheduled = True
        self._scheduled_by_timestamp: dict[str, bool] = {}
        self.decision_events: list[dict[str, object]] = []
        self.observation_events: list[dict[str, object]] = []
        self.duplicate_observation_attempts = 0
        self._last_snapshot_equity: float | None = None
        if state_snapshot is not None:
            self.restore_snapshot(state_snapshot)

    def _episode_id(self, symbol: str) -> str:
        value = f"{symbol}:episode-{self._next_episode_number:06d}"
        self._next_episode_number += 1
        return value

    def _ensure_record(self, symbol: str, state: EpisodeState) -> dict[str, object]:
        record = self._open_records.get(symbol)
        if record is None or record.get("episode_id") != state.episode_id:
            record = _new_episode_record(state, symbol)
            self._open_records[symbol] = record
            self.exit_ledger.append(record)
        return record

    def sync_authoritative_positions(self, positions: Mapping[str, tuple[float, float]]) -> None:
        """Mirror engine positions without interpreting quantity changes as BUY."""
        current = {str(symbol): (float(qty), float(basis)) for symbol, (qty, basis) in positions.items() if qty > 0}
        for symbol in tuple(self.states):
            if symbol not in current:
                self.states.pop(symbol, None)
                self._open_records.pop(symbol, None)
                self.managed_symbols.discard(symbol)
        for symbol, (qty, basis) in current.items():
            if symbol not in self.managed_symbols:
                # Backtest positions created outside an explicit BUY fill are
                # not candidate-owned and must not be protected.
                continue
            state = self.states.get(symbol)
            if state is None:
                state = EpisodeState(qty, basis, episode_id=self._episode_id(symbol))
                self.states[symbol] = state
                self._ensure_record(symbol, state)
            else:
                state.quantity = qty
                state.basis = basis
            record = self._ensure_record(symbol, state)
            record["managed_weighted_basis"] = state.basis
            record["remaining_quantity"] = state.quantity

    def _ranked_alpha(self, context: PortfolioContext) -> dict[str, int]:
        if self.candidate != "E3_PROFIT_ALPHA_CONFIRMED":
            return {}
        ranked, _ = self._score_candidates(context)
        return {symbol: index for index, (_score, symbol) in enumerate(ranked, start=1)}

    def observe_completed_close(self, context: PortfolioContext) -> None:
        """Observe exactly once per managed position and completed timestamp."""
        ranks = self._ranked_alpha(context)
        timestamp = context.decision_timestamp
        for symbol in sorted(self.states):
            state = self.states[symbol]
            close = context.prices.get(symbol)
            if close is None or close <= 0 or state.quantity <= 0:
                continue
            before = state.last_observed_timestamp
            triggered = state.observe(
                float(close),
                self.candidate,
                qualified=symbol in ranks,
                rank=ranks.get(symbol, 999),
                timestamp=timestamp,
            )
            if before == state.last_observed_timestamp:
                self.duplicate_observation_attempts += 1
                continue
            self.observation_events.append(
                {
                    "symbol": symbol,
                    "timestamp": timestamp.isoformat(),
                    "close": float(close),
                    "qualified": symbol in ranks,
                    "rank": ranks.get(symbol, 999),
                }
            )
            record = self._ensure_record(symbol, state)
            record["managed_weighted_basis"] = state.basis
            record["activation_time"] = state.activation_time
            record["activation_price"] = state.activation_price
            record["high_close"] = state.high_close
            record["floor_price"] = state.floor_price
            record["alpha_evidence"] = dict(state.alpha_evidence)
            if triggered:
                self._pending_triggers.add(symbol)
                record["trigger_time"] = state.trigger_timestamp
                record["trigger_close"] = state.trigger_close
                record["intended_reason"] = state.intended_reason

    def should_trigger_intermediate_exit(self, context: PortfolioContext) -> bool:
        del context
        return any(state.pending and state.quantity > 0 for state in self.states.values())

    def requires_intermediate_risk_rebalance(self, context: PortfolioContext) -> bool:  # type: ignore[override]
        return self.should_trigger_intermediate_exit(context)

    def set_decision_schedule(self, timestamp: datetime, scheduled: bool) -> None:
        self._decision_is_scheduled = scheduled
        self._scheduled_by_timestamp[timestamp.isoformat()] = scheduled

    def _mark_scheduled_signal_exits(self, context: PortfolioContext, target: TargetPortfolio) -> None:
        target_symbols = {weight.symbol for weight in target.weights if weight.weight > 0}
        for symbol in sorted(context.holdings):
            if context.holdings[symbol] <= 0 or symbol in target_symbols:
                continue
            state = self.states.get(symbol)
            if state is None:
                continue
            state.trigger_timestamp = context.decision_timestamp.isoformat()
            state.trigger_close = context.prices.get(symbol)
            state.intended_reason = "scheduled_v3_signal"
            record = self._ensure_record(symbol, state)
            record["trigger_time"] = state.trigger_timestamp
            record["trigger_close"] = state.trigger_close
            record["intended_reason"] = state.intended_reason

    def scheduled_v3_decide(self, context: PortfolioContext) -> TargetPortfolio:
        """Call the exact instrumented current V3 decision path."""
        return roadmap.RoadmapControl.decide(self, context)

    def decide(self, context: PortfolioContext) -> TargetPortfolio:  # type: ignore[override]
        scheduled = self._decision_is_scheduled
        if scheduled:
            target = self.scheduled_v3_decide(context)
            self._mark_scheduled_signal_exits(context, target)
            attribution = {
                symbol: (
                    "scheduled_v3_signal" if target.weight_for(symbol, context.market) <= 0 else "scheduled_v3_retained"
                )
                for symbol in context.holdings
                if context.holdings[symbol] > 0
            }
            target = dataclasses.replace(
                target,
                metadata={
                    **target.metadata,
                    "research_candidate": self.candidate,
                    "decision_attribution": attribution,
                },
            )
        else:
            triggers = {symbol for symbol, state in self.states.items() if state.pending and state.quantity > 0}
            weights = []
            for symbol, quantity in sorted(context.holdings.items()):
                if symbol in triggers:
                    continue
                price = context.prices.get(symbol, 0.0)
                weight = quantity * price / max(context.equity, 1.0)
                if weight > 0:
                    weights.append(TargetWeight(symbol, weight, "profit_protection_hold"))
            attribution = {symbol: "profit_protection" for symbol in sorted(triggers)}
            target = TargetPortfolio(
                strategy_name=self.name,
                strategy_version=self.version,
                decision_timestamp=context.decision_timestamp,
                data_timestamp=context.decision_timestamp,
                weights=tuple(weights),
                cash_weight=max(0.0, 1.0 - sum(weight.weight for weight in weights)),
                rebalance_reason=RebalanceReason.RISK_OFF,
                universe_version=context.universe_version,
                universe_snapshot_id=context.universe_version,
                parameters=self.parameter_tuple(),
                metadata={
                    "research_candidate": self.candidate,
                    "triggered": sorted(triggers),
                    "decision_attribution": attribution,
                    "intermediate_risk_reduction": True,
                },
            )
            self._pending_triggers.difference_update(triggers)
        self.decision_events.append(
            {
                "timestamp": context.decision_timestamp.isoformat(),
                "scheduled": scheduled,
                "target_symbols": sorted(weight.symbol for weight in target.weights),
                "target_exposure": sum(weight.weight for weight in target.weights),
                "attribution": target.metadata.get("decision_attribution", {}),
            }
        )
        return target

    def on_fill(self, fill: PortfolioFill, account_equity: float) -> None:
        symbol = fill.symbol
        if fill.side == OrderSide.BUY:
            state = self.states.get(symbol)
            if state is None or state.quantity <= 0:
                state = EpisodeState(0.0, fill.fill_price, episode_id=self._episode_id(symbol))
                self.states[symbol] = state
                self.managed_symbols.add(symbol)
            state.apply_buy(fill.qty, fill.fill_price, fill.timestamp)
            self.managed_symbols.add(symbol)
            record = self._ensure_record(symbol, state)
            record["entry_time"] = state.entry_time
            record["managed_weighted_basis"] = state.basis
            record["quantity_initial"] = max(float(record.get("quantity_initial") or 0.0), state.quantity)
            record["remaining_quantity"] = state.quantity
            record["contemporaneous_account_equity_impact"] = account_equity
            return
        state = self.states.get(symbol)
        if state is None:
            return
        record = self._ensure_record(symbol, state)
        before = state.quantity
        state.apply_sell(fill.qty)
        record["quantity_exited"] = float(record.get("quantity_exited") or 0.0) + fill.qty
        record["partial_fill_count"] = int(record.get("partial_fill_count") or 0) + 1
        record["remaining_quantity"] = state.quantity
        record["actual_fill_time"] = record.get("actual_fill_time") or fill.timestamp.isoformat()
        old_qty = float(record.get("_fill_qty") or 0.0)
        old_px = float(record.get("actual_fill_price") or 0.0)
        record["actual_fill_price"] = (
            (old_px * old_qty + fill.fill_price * fill.qty) / (old_qty + fill.qty)
            if old_qty + fill.qty > 0
            else fill.fill_price
        )
        record["_fill_qty"] = old_qty + fill.qty
        trigger_time = _timestamp(record.get("trigger_time"))
        if trigger_time is not None:
            record["trigger_fill_gap"] = (_timestamp(fill.timestamp) - trigger_time).total_seconds()
        record["incremental_costs"] = float(record.get("incremental_costs") or 0.0) + (
            fill.slippage_cost + fill.spread_cost + fill.fee
        )
        if state.quantity <= 1e-12 or fill.qty >= before - 1e-12:
            current_profit = fill.fill_price / state.basis - 1.0 if state.basis > 0 else 0.0
            peak = float(record.get("peak_open_profit") or 0.0)
            record["peak_to_exit_giveback"] = max(0.0, peak - current_profit)
            record["remaining_quantity"] = 0.0
            record.pop("_fill_qty", None)
            self._open_records.pop(symbol, None)
            self.states.pop(symbol, None)
            self.managed_symbols.discard(symbol)

    def on_corporate_action(self, action: CorporateAction) -> None:
        if action.action_type != CorporateActionType.SPLIT:
            return
        state = self.states.get(action.symbol)
        if state is None:
            return
        state.apply_split(action.value)
        record = self._ensure_record(action.symbol, state)
        record["managed_weighted_basis"] = state.basis
        record["high_close"] = state.high_close
        record["floor_price"] = state.floor_price
        record["activation_price"] = state.activation_price

    def on_terminal(self, symbol: str, event: Mapping[str, object]) -> None:
        state = self.states.get(symbol)
        if state is None:
            return
        record = self._ensure_record(symbol, state)
        quantity = float(event.get("quantity", state.quantity))
        state.apply_sell(quantity)
        record["quantity_exited"] = float(record.get("quantity_exited") or 0.0) + quantity
        record["partial_fill_count"] = int(record.get("partial_fill_count") or 0) + 1
        record["remaining_quantity"] = 0.0
        record["actual_fill_time"] = event.get("event_timestamp")
        proceeds = float(event.get("proceeds", 0.0) or 0.0)
        record["actual_fill_price"] = proceeds / quantity if quantity > 0 else None
        record["incremental_costs"] = float(record.get("incremental_costs") or 0.0) + float(
            event.get("fees", 0.0) or 0.0
        )
        record["peak_to_exit_giveback"] = max(
            0.0,
            float(record.get("peak_open_profit") or 0.0)
            - (float(record.get("actual_fill_price") or 0.0) / state.basis - 1.0 if state.basis > 0 else 0.0),
        )
        self._open_records.pop(symbol, None)
        self.states.pop(symbol, None)
        self.managed_symbols.discard(symbol)

    def record_snapshot(self, prices: Mapping[str, float], account_equity: float) -> None:
        self._last_snapshot_equity = account_equity
        for symbol, state in self.states.items():
            close = float(prices.get(symbol, 0.0) or 0.0)
            if close <= 0 or state.basis <= 0:
                continue
            profit = close / state.basis - 1.0
            record = self._ensure_record(symbol, state)
            record["peak_open_profit"] = max(float(record.get("peak_open_profit") or 0.0), profit)
            record["mfe"] = max(float(record.get("mfe") or 0.0), profit)
            record["mae"] = min(float(record.get("mae") or 0.0), profit)
            record["remaining_quantity"] = state.quantity
            record["contemporaneous_account_equity_impact"] = account_equity

    def snapshot_state(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "candidate": self.candidate,
            "next_episode_number": self._next_episode_number,
            "managed_symbols": sorted(self.managed_symbols),
            "states": {symbol: state.snapshot() for symbol, state in sorted(self.states.items())},
            "exit_ledger": self.exit_ledger,
            "observation_events": self.observation_events,
            "decision_events": self.decision_events,
        }

    def restore_snapshot(self, raw: Mapping[str, object]) -> None:
        if str(raw.get("candidate", self.candidate)) != self.candidate:
            raise ValueError("Episode sidecar candidate mismatch")
        self._next_episode_number = int(raw.get("next_episode_number", 1))
        self.managed_symbols = {str(symbol) for symbol in raw.get("managed_symbols", ())}
        self.states = {
            str(symbol): EpisodeState.restore(dict(value)) for symbol, value in dict(raw.get("states", {})).items()
        }
        self.exit_ledger = [dict(item) for item in raw.get("exit_ledger", ())]
        self._open_records = {
            str(record["symbol"]): record
            for record in self.exit_ledger
            if record.get("symbol") in self.states and record.get("remaining_quantity", 0) > 0
        }
        self.observation_events = [dict(item) for item in raw.get("observation_events", ())]
        self.decision_events = [dict(item) for item in raw.get("decision_events", ())]


@dataclass
class CaseRun:
    candidate: str
    window_key: str
    result: PortfolioBacktestResult
    strategy: object
    engine: ResearchEngine
    window: roadmap.PreparedWindow
    cost_multiplier: float = 1.0


class ResearchEngine(TerminalAwareResearchEngine):
    """Current terminal engine with a research-only episode sidecar."""

    def __init__(
        self,
        *args: Any,
        episode_state_path: str | Path | None = None,
        regulatory_fee_multiplier: float = 1.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.episode_state_path = Path(episode_state_path) if episode_state_path else None
        self.regulatory_fee_multiplier = float(regulatory_fee_multiplier)
        self.incomplete_strategy_batches = 0
        self.observation_events: list[dict[str, object]] = []
        self.duplicate_observation_attempts = 0
        self.intermediate_exposure_violations: list[dict[str, object]] = []
        self.off_schedule_buys: list[dict[str, object]] = []
        self._scheduled_by_timestamp: dict[str, bool] = {}
        self.exit_ledger: list[dict[str, object]] = []
        self._generic_open: dict[str, dict[str, object]] = {}
        if self.episode_state_path is not None and isinstance(self.strategy, ProfitProtectionStrategy):
            if self.episode_state_path.exists():
                raw = json.loads(self.episode_state_path.read_text(encoding="utf-8"))
                self.strategy.restore_snapshot(dict(raw))

    def _equity_fee(self, side: OrderSide, quantity: float, price: float, trade_date: Any) -> float:  # type: ignore[override]
        fee = super()._equity_fee(side, quantity, price, trade_date)
        return fee * self.regulatory_fee_multiplier

    def _context_complete(self, timestamp: datetime, context: PortfolioContext) -> bool:
        expected = self._effective_observation_symbols(timestamp)
        if expected is None:
            return True
        return not any(
            symbol not in context.history
            or not context.history[symbol]
            or context.history[symbol][-1].timestamp != timestamp
            for symbol in expected
        )

    def _decide(self, timestamp: datetime) -> None:  # type: ignore[override]
        market = self.strategy.market
        previous = self._previous_timestamp[market]
        scheduled = self.rebalance_policy.is_period_start(previous, timestamp)
        self._scheduled_by_timestamp[timestamp.isoformat()] = scheduled
        if hasattr(self.strategy, "sync_authoritative_positions"):
            positions = {symbol: (position.qty, position.avg_price) for symbol, position in self._positions.items()}
            self.strategy.sync_authoritative_positions(positions)  # type: ignore[attr-defined]
        if hasattr(self.strategy, "set_decision_schedule"):
            self.strategy.set_decision_schedule(timestamp, scheduled)  # type: ignore[attr-defined]
        if hasattr(self.strategy, "observe_completed_close"):
            context = self._build_context(timestamp)
            if self._context_complete(timestamp, context):
                self.strategy.observe_completed_close(context)  # type: ignore[attr-defined]
                self.observation_events = list(getattr(self.strategy, "observation_events", ()))
                self.duplicate_observation_attempts = int(getattr(self.strategy, "duplicate_observation_attempts", 0))
            else:
                self.incomplete_strategy_batches += 1
        super()._decide(timestamp)

    def _apply_target(self, target: TargetPortfolio) -> None:  # type: ignore[override]
        scheduled = self._scheduled_by_timestamp.get(target.decision_timestamp.isoformat(), True)
        if not scheduled:
            equity = self._equity()
            current = (
                sum(
                    position.qty * self._last_price.get(symbol, position.avg_price)
                    for symbol, position in self._positions.items()
                )
                / equity
                if equity > 0
                else 0.0
            )
            requested = sum(weight.weight for weight in target.weights)
            if requested > current + 1e-9:
                self.intermediate_exposure_violations.append(
                    {
                        "timestamp": target.decision_timestamp.isoformat(),
                        "current_exposure": current,
                        "requested_exposure": requested,
                    }
                )
        super()._apply_target(target)

    def _fill_side(self, bar: Bar, side: OrderSide) -> None:  # type: ignore[override]
        before = len(self._fills)
        super()._fill_side(bar, side)
        for fill in self._fills[before:]:
            scheduled = self._scheduled_by_timestamp.get(fill.decision_timestamp.isoformat(), True)
            if fill.side == OrderSide.BUY and not scheduled:
                self.off_schedule_buys.append(
                    {
                        "timestamp": fill.timestamp.isoformat(),
                        "symbol": fill.symbol,
                        "decision": fill.decision_timestamp.isoformat(),
                    }
                )
            if hasattr(self.strategy, "on_fill"):
                self.strategy.on_fill(fill, self._equity())  # type: ignore[attr-defined]
            else:
                self._record_generic_fill(fill)
        self.exit_ledger = list(getattr(self.strategy, "exit_ledger", self.exit_ledger))

    def _record_generic_fill(self, fill: PortfolioFill) -> None:
        if fill.side == OrderSide.BUY:
            self._generic_open.setdefault(
                fill.symbol,
                {
                    "episode_id": f"{fill.symbol}:control",
                    "symbol": fill.symbol,
                    "entry_time": fill.timestamp.isoformat(),
                    "managed_weighted_basis": fill.fill_price,
                    "quantity_initial": fill.qty,
                },
            )
            return
        record = self._generic_open.get(fill.symbol)
        if record is None:
            return
        record.update(
            {
                "trigger_time": fill.decision_timestamp.isoformat(),
                "trigger_close": fill.reference_price,
                "intended_reason": "scheduled_v3_signal",
                "actual_fill_time": fill.timestamp.isoformat(),
                "actual_fill_price": fill.fill_price,
                "quantity_exited": fill.qty,
                "remaining_quantity": 0.0,
                "incremental_costs": fill.slippage_cost + fill.spread_cost + fill.fee,
                "net_protection_value": 0.0,
            }
        )
        complete = {field: record.get(field) for field in REQUIRED_LEDGER_FIELDS}
        self.exit_ledger.append(complete)
        self._generic_open.pop(fill.symbol, None)

    def _apply_corporate_action(self, action: CorporateAction) -> None:  # type: ignore[override]
        super()._apply_corporate_action(action)
        if hasattr(self.strategy, "on_corporate_action"):
            self.strategy.on_corporate_action(action)  # type: ignore[attr-defined]

    def _execute_terminal(self, symbol: str, position: Any, last_close: float, terminal_date: datetime) -> None:
        before = len(self.terminal_events_fired)
        super()._execute_terminal(symbol, position, last_close, terminal_date)
        for event in self.terminal_events_fired[before:]:
            if hasattr(self.strategy, "on_terminal"):
                self.strategy.on_terminal(symbol, event)  # type: ignore[attr-defined]
        self.exit_ledger = list(getattr(self.strategy, "exit_ledger", self.exit_ledger))

    def _record_holdings_snapshot(self, timestamp: datetime) -> None:  # type: ignore[override]
        super()._record_holdings_snapshot(timestamp)
        if hasattr(self.strategy, "record_snapshot"):
            self.strategy.record_snapshot(self._last_price, self._equity())  # type: ignore[attr-defined]
            self.exit_ledger = list(getattr(self.strategy, "exit_ledger", self.exit_ledger))
        if self.episode_state_path is not None and hasattr(self.strategy, "snapshot_state"):
            self.episode_state_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(
                self.episode_state_path,
                json.dumps(self.strategy.snapshot_state(), indent=2, sort_keys=True, allow_nan=False),  # type: ignore[attr-defined]
            )


def make_engine(
    config: Config,
    strategy: PortfolioStrategy,
    window: roadmap.PreparedWindow,
    *,
    terminal_map: Mapping[str, TerminalInfo],
    cost_multiplier: float = 1.0,
    episode_state_path: str | Path | None = None,
) -> ResearchEngine:
    """Construct the current-parity engine with research-only hooks."""
    return ResearchEngine(
        strategy=strategy,
        rebalance_policy=roadmap.rebalance_policy_for(strategy),
        terminal_map=dict(terminal_map),
        policy="T1",
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * cost_multiplier,
        spread_bps=config.backtest.spread_bps * cost_multiplier,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * cost_multiplier,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        benchmark_symbol="SPY",
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        enforce_drawdown_halt=False,
        universe=window.universe,
        universe_version=window.universe_version,
        episode_state_path=episode_state_path,
        regulatory_fee_multiplier=cost_multiplier,
    )


def build_strategy(candidate: str) -> PortfolioStrategy:
    if candidate == "E0_CONTROL":
        return roadmap.build_strategy("control")
    return ProfitProtectionStrategy(candidate)


def run_case(
    config: Config,
    candidate: str,
    window: roadmap.PreparedWindow,
    *,
    terminal_map: Mapping[str, TerminalInfo],
    actions: Sequence[CorporateAction],
    cost_multiplier: float = 1.0,
    episode_state_path: str | Path | None = None,
) -> CaseRun:
    strategy = build_strategy(candidate)
    engine = make_engine(
        config,
        strategy,
        window,
        terminal_map=terminal_map,
        cost_multiplier=cost_multiplier,
        episode_state_path=episode_state_path,
    )
    result = engine.run(
        window.filtered,
        warmup=window.warmup,
        liquidate_at_end=True,
        corporate_actions=list(actions),
        prepared_inputs=window.prepared,
        numeric_history=window.numeric_history,
    )
    if not result.equity_curve:
        raise RuntimeError(f"empty result for {candidate}/{window.key}")
    return CaseRun(candidate, window.key, result, strategy, engine, window, cost_multiplier)


def _result_metrics(result: PortfolioBacktestResult) -> dict[str, object]:
    return roadmap.full_stats(result)


def _case_payload(case: CaseRun) -> dict[str, object]:
    return {
        "candidate": case.candidate,
        "window": case.window_key,
        "cost_multiplier": case.cost_multiplier,
        "metrics": _result_metrics(case.result),
        "components": parity_result_components(case.result, case.engine, case.window),
        "counts": {
            "rebalances": len(case.result.rebalances),
            "fills": len(case.result.fills),
            "terminal_events": len(case.engine.terminal_events_fired),
            "incomplete_strategy_batches": case.engine.incomplete_strategy_batches,
        },
        "observation_audit": {
            "observation_count": len(case.engine.observation_events),
            "duplicate_observation_attempts": case.engine.duplicate_observation_attempts,
            "observed_timestamps_by_symbol": _observed_timestamps(case.engine.observation_events),
        },
        "execution_audit": {
            "intermediate_exposure_violations": case.engine.intermediate_exposure_violations,
            "off_schedule_buys": case.engine.off_schedule_buys,
        },
        "exit_ledger": getattr(case.strategy, "exit_ledger", case.engine.exit_ledger),
    }


def _observed_timestamps(events: Sequence[Mapping[str, object]]) -> dict[str, list[str]]:
    by_symbol: dict[str, list[str]] = {}
    for event in events:
        by_symbol.setdefault(str(event["symbol"]), []).append(str(event["timestamp"]))
    return {symbol: values for symbol, values in sorted(by_symbol.items())}


def _required_ledger_complete(ledger: Sequence[Mapping[str, object]]) -> bool:
    return all(set(REQUIRED_LEDGER_FIELDS).issubset(record) for record in ledger)


def _annotate_control_comparison(candidate: CaseRun, control: CaseRun) -> dict[str, float]:
    """Pair early exits with the next control sell for an auditable diagnostic."""
    control_sells: dict[str, list[PortfolioFill]] = {}
    for fill in control.result.fills:
        if fill.side == OrderSide.SELL:
            control_sells.setdefault(fill.symbol, []).append(fill)
    avoided = 0.0
    missed = 0.0
    for record in getattr(candidate.strategy, "exit_ledger", ()):
        reason = str(record.get("intended_reason") or "")
        if not reason.startswith("profit_") and reason != "take_profit_5pct_close":
            continue
        symbol = str(record.get("symbol"))
        trigger = _timestamp(record.get("trigger_time"))
        actual = _timestamp(record.get("actual_fill_time"))
        actual_px = float(record.get("actual_fill_price") or 0.0)
        qty = float(record.get("quantity_exited") or 0.0)
        if trigger is None or actual is None or actual_px <= 0 or qty <= 0:
            continue
        later = [fill for fill in control_sells.get(symbol, ()) if fill.timestamp >= trigger]
        if not later:
            continue
        control_fill = later[0]
        control_px = control_fill.fill_price
        delta = (actual_px - control_px) * qty
        if delta >= 0:
            avoided += delta
        else:
            missed += -delta
        record["control_exit_time"] = control_fill.timestamp.isoformat()
        record["control_exit_price"] = control_px
        record["avoided_loss_or_giveback"] = max(0.0, delta)
        record["missed_upside"] = max(0.0, -delta)
    incremental_costs = (
        candidate.result.slippage_cost_total
        + candidate.result.spread_cost_total
        + candidate.result.fee_total
        - control.result.slippage_cost_total
        - control.result.spread_cost_total
        - control.result.fee_total
    )
    net = avoided - missed - incremental_costs
    for record in getattr(candidate.strategy, "exit_ledger", ()):
        if (
            str(record.get("intended_reason") or "").startswith("profit_")
            or record.get("intended_reason") == "take_profit_5pct_close"
        ):
            record["incremental_costs"] = incremental_costs
            record["net_protection_value"] = net
    return {
        "avoided_loss_or_giveback": avoided,
        "missed_upside": missed,
        "incremental_costs": incremental_costs,
        "net_protection_value": net,
    }


def _stage_one_gate_values(
    candidate_full: CaseRun,
    candidate_recent: CaseRun,
    control_full: CaseRun,
    control_recent: CaseRun,
    protection_summary: Mapping[str, float],
    *,
    control_parity: bool,
) -> dict[str, bool]:
    full = _result_metrics(candidate_full.result)
    recent = _result_metrics(candidate_recent.result)
    control_full_metrics = _result_metrics(control_full.result)
    control_recent_metrics = _result_metrics(control_recent.result)
    ledger = getattr(candidate_full.strategy, "exit_ledger", ())
    return {
        "control_parity": control_parity,
        "complete_run_no_incomplete_batches": (
            candidate_full.engine.incomplete_strategy_batches == 0
            and candidate_recent.engine.incomplete_strategy_batches == 0
        ),
        "full_sharpe_not_below_control": float(full["sharpe"]) >= float(control_full_metrics["sharpe"]),
        "full_return_floor": float(full["return"]) >= float(control_full_metrics["return"]) - 0.05,
        "max_drawdown_not_materially_worse": float(full["max_drawdown"])
        <= float(control_full_metrics["max_drawdown"]) + 0.01,
        "net_protection_value_positive": float(protection_summary.get("net_protection_value", 0.0)) > 0.0,
        "recent_sharpe_not_below_control": float(recent["sharpe"]) >= float(control_recent_metrics["sharpe"]),
        "no_intermediate_exposure_increase": not (
            candidate_full.engine.intermediate_exposure_violations
            or candidate_recent.engine.intermediate_exposure_violations
        ),
        "no_intra_week_buy_or_replacement": not (
            candidate_full.engine.off_schedule_buys or candidate_recent.engine.off_schedule_buys
        ),
        "strict_observation_idempotency": (
            candidate_full.engine.duplicate_observation_attempts == 0
            and candidate_recent.engine.duplicate_observation_attempts == 0
            and _observation_sequences_are_strict(candidate_full.engine.observation_events)
            and _observation_sequences_are_strict(candidate_recent.engine.observation_events)
        ),
        "complete_exit_ledger": _required_ledger_complete(ledger),
    }


def _observation_sequences_are_strict(events: Sequence[Mapping[str, object]]) -> bool:
    for timestamps in _observed_timestamps(events).values():
        parsed = [_timestamp(value) for value in timestamps]
        if any(value is None for value in parsed):
            return False
        if len({value for value in parsed}) != len(parsed):
            return False
        if parsed != sorted(parsed):
            return False
    return True


def _run_current_control_reference(output: Path) -> dict[str, object]:
    import research_v3_current_parity_control as current_control

    return current_control.run(output)


def run_control_parity(
    config: Config,
    window: roadmap.PreparedWindow,
    *,
    terminal_map: Mapping[str, TerminalInfo],
    actions: Sequence[CorporateAction],
    reference_output: Path,
) -> tuple[dict[str, object], list[CaseRun], bool]:
    reference = _run_current_control_reference(reference_output)
    runs = [
        run_case(config, "E0_CONTROL", window, terminal_map=terminal_map, actions=actions),
        run_case(config, "E0_CONTROL", window, terminal_map=terminal_map, actions=actions),
    ]
    reference_components = dict(reference["components"])
    reference_metrics = dict(reference["metrics"])
    reference_counts = dict(reference["counts"])
    components_match = all(
        parity_result_components(case.result, case.engine, case.window) == reference_components for case in runs
    )
    both_equal = parity_result_components(runs[0].result, runs[0].engine, runs[0].window) == parity_result_components(
        runs[1].result, runs[1].engine, runs[1].window
    )
    metrics_match = all(_result_metrics(case.result) == reference_metrics for case in runs)
    counts_match = all(
        {
            "rebalance_decisions": len(case.result.rebalances),
            "fills": len(case.result.fills),
            "terminal_events": len(case.engine.terminal_events_fired),
            "incomplete_strategy_batches": case.engine.incomplete_strategy_batches,
        }
        == reference_counts
        for case in runs
    )
    valid = (
        components_match
        and both_equal
        and metrics_match
        and counts_match
        and all(case.engine.incomplete_strategy_batches == 0 for case in runs)
    )
    if not valid:
        raise ResearchBlocked(
            "BLOCKED_CONTROL_PARITY_FAILURE",
            json.dumps(
                {
                    "components_match": components_match,
                    "both_equal": both_equal,
                    "metrics_match": metrics_match,
                    "counts_match": counts_match,
                },
                sort_keys=True,
            ),
        )
    return reference, runs, True


def _jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(_jsonable(payload), indent=2, sort_keys=True, allow_nan=False) + "\n")


def _manifest_digest(config: Config) -> str:
    return roadmap.config_digest(config)


def _load_inputs() -> tuple[
    Config, list[Bar], dict[str, TerminalInfo], list[CorporateAction], dict[str, dict[str, object]]
]:
    identity = verify_required_data()
    config = load_config(str(REPO / "config/settings.yaml"))
    if float(config.strategy.equity.maximum_allocation) != roadmap.CURRENT_EQUITY_ALLOCATION:
        raise ResearchBlocked("BLOCKED_CONTROL_PARITY_FAILURE", "equity allocation is not 0.55")
    if config.strategy.equity.name != "equity_residual_momentum_v3":
        raise ResearchBlocked("BLOCKED_CONTROL_PARITY_FAILURE", "configured equity strategy is not current V3")
    if dict(config.strategy.equity.parameters) != FROZEN_PARAMETERS:
        raise ResearchBlocked("BLOCKED_CONTROL_PARITY_FAILURE", "configured V3 parameters changed")
    bars = roadmap.load_bars(roadmap.DATASET_V5)
    terminal_map, _ = roadmap.load_lifetimes()
    actions = roadmap.load_corporate_actions_all()
    return config, bars, terminal_map, actions, identity


def _prepare_windows(config: Config, bars: list[Bar]) -> dict[str, roadmap.PreparedWindow]:
    return {
        "full": roadmap.prepare_window(
            config,
            bars,
            key="equity-profit-protection-r2-full",
            start=None,
            end=roadmap.EVAL_END,
        ),
        "recent": roadmap.prepare_window(
            config,
            bars,
            key="equity-profit-protection-r2-recent",
            start=roadmap.RECENT_START,
            end=roadmap.EVAL_END,
        ),
    }


def _run_robustness(
    config: Config,
    bars: list[Bar],
    terminal_map: Mapping[str, TerminalInfo],
    actions: Sequence[CorporateAction],
    survivor: str,
) -> dict[str, object]:
    """Run declared robustness only after a Stage One survivor exists."""
    folds: list[dict[str, object]] = []
    for fold_id, start, end in roadmap.fold_windows():
        window = roadmap.prepare_window(config, bars, key=f"{survivor}-{fold_id}", start=start, end=end)
        control = run_case(config, "E0_CONTROL", window, terminal_map=terminal_map, actions=actions)
        candidate = run_case(config, survivor, window, terminal_map=terminal_map, actions=actions)
        folds.append(
            {
                "fold": fold_id,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "control": _result_metrics(control.result),
                "candidate": _result_metrics(candidate.result),
                "independent_run": True,
            }
        )
    cost_stress: list[dict[str, object]] = []
    for multiplier in (2.0, 3.0, 5.0):
        window = roadmap.prepare_window(
            config,
            bars,
            key=f"{survivor}-cost-{multiplier:g}",
            start=None,
            end=roadmap.EVAL_END,
        )
        control = run_case(
            config,
            "E0_CONTROL",
            window,
            terminal_map=terminal_map,
            actions=actions,
            cost_multiplier=multiplier,
        )
        candidate = run_case(
            config,
            survivor,
            window,
            terminal_map=terminal_map,
            actions=actions,
            cost_multiplier=multiplier,
        )
        cost_stress.append(
            {
                "multiplier": multiplier,
                "control": _result_metrics(control.result),
                "candidate": _result_metrics(candidate.result),
                "independent_run": True,
            }
        )
    return {
        "status": "executed",
        "survivor": survivor,
        "walk_forward": folds,
        "cost_stress": cost_stress,
        "contract": {
            "independent_backtest_per_fold": True,
            "independent_backtest_per_cost_level": True,
            "no_curve_slicing": True,
            "no_heuristic_fee_adjustment": True,
        },
    }


def run_research(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    reference_output: Path = DEFAULT_REFERENCE_RECEIPT,
) -> dict[str, object]:
    config, bars, terminal_map, actions, dataset_identity = _load_inputs()
    ca_audit = corporate_action_source_audit()
    windows = _prepare_windows(config, bars)
    reference, parity_runs, parity_ok = run_control_parity(
        config,
        windows["full"],
        terminal_map=terminal_map,
        actions=actions,
        reference_output=reference_output,
    )
    control_full = parity_runs[0]
    control_recent = run_case(
        config,
        "E0_CONTROL",
        windows["recent"],
        terminal_map=terminal_map,
        actions=actions,
    )
    cases: dict[str, dict[str, object]] = {}
    survivors: list[str] = []
    for candidate in PROTECTION_CANDIDATES:
        full = run_case(
            config,
            candidate,
            windows["full"],
            terminal_map=terminal_map,
            actions=actions,
        )
        recent = run_case(
            config,
            candidate,
            windows["recent"],
            terminal_map=terminal_map,
            actions=actions,
        )
        protection = _annotate_control_comparison(full, control_full)
        gates = _stage_one_gate_values(
            full,
            recent,
            control_full,
            control_recent,
            protection,
            control_parity=parity_ok,
        )
        payload = {
            "candidate": candidate,
            "full": _case_payload(full),
            "recent": _case_payload(recent),
            "protection_comparison": protection,
            "stage_one_gates": gates,
            "stage_one_passed": all(gates.values()),
        }
        cases[candidate] = payload
        if payload["stage_one_passed"]:
            survivors.append(candidate)
        write_json(output_dir / f"{candidate}.json", payload)

    robustness: dict[str, object]
    if survivors:
        robustness = _run_robustness(config, bars, terminal_map, actions, survivors[0])
        classification = "CANDIDATE_FOR_SEPARATE_PAPER_ARCHITECTURE_REVIEW"
    else:
        robustness = {"status": "not_run", "reason": "no Stage One survivor; frozen stop condition"}
        classification = "KEEP_CURRENT_V3_AFTER_VALID_COMPARISON"
    stage_one = {
        "schema_version": "1.0.0",
        "mission": MISSION,
        "current_git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "config_digest_sha256": _manifest_digest(config),
        "dataset": dataset_identity,
        "corporate_action_audit": ca_audit,
        "current_control_reference": {
            "receipt_path": str(reference_output),
            "components": reference["components"],
            "metrics": reference["metrics"],
            "counts": reference["counts"],
            "r2_e0_replay_count": 2,
            "r2_e0_replay_components": [
                parity_result_components(case.result, case.engine, case.window) for case in parity_runs
            ],
        },
        "control_full": _case_payload(control_full),
        "control_recent": _case_payload(control_recent),
        "candidates": cases,
        "stage_one_survivors": survivors,
        "robustness": robustness,
        "classification": classification,
        "research_only": True,
        "production_registration_or_promotion": False,
    }
    write_json(output_dir / "stage_one.json", stage_one)
    artifact_hashes = {
        path.name: _sha256(path) for path in sorted(output_dir.glob("*.json")) if path.name != "artifact_hashes.json"
    }
    write_json(output_dir / "artifact_hashes.json", {"artifacts": artifact_hashes})
    return stage_one | {"artifact_hashes": artifact_hashes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--reference-output", type=Path, default=DEFAULT_REFERENCE_RECEIPT)
    args = parser.parse_args()
    try:
        payload = run_research(output_dir=args.output_dir, reference_output=args.reference_output)
    except ResearchBlocked as exc:
        print(exc.code)
        return 2
    print(json.dumps(_jsonable(payload), indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
