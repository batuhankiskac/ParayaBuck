"""Approval-gated portfolio decision runtime shared by shadow and broker stages."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from parayabuck.core.models import AccountSnapshot, Bar, Market
from parayabuck.execution.portfolio import (
    AssetTradingRules,
    PortfolioOrderRequest,
    PortfolioRiskOverlay,
    RiskOverlayDecision,
    TargetToOrderPlanner,
)
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.approval import (
    CURRENT_GATE_POLICY_VERSION,
    ApprovalError,
    ApprovalStage,
    StrategyApprovalManifest,
)
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import TargetPortfolio, TargetWeight
from parayabuck.storage import SQLiteStore
from parayabuck.utils.io import atomic_write


class PortfolioRuntimeBroker(Protocol):
    def account(self) -> AccountSnapshot: ...

    def asset_trading_rules(self, symbol: str, market: Market) -> AssetTradingRules: ...

    def open_portfolio_order_symbols(self) -> set[str]: ...

    def submit_portfolio_order(self, order: PortfolioOrderRequest) -> str: ...


class PortfolioDecisionService:
    """Build a strategy context exclusively from finalized approved bars."""

    def build_context(
        self,
        manifest: StrategyApprovalManifest,
        strategy: PortfolioStrategy,
        history: dict[str, list[Bar]],
        account: AccountSnapshot,
        decision_timestamp: datetime,
    ) -> PortfolioContext:
        if any(
            not series or series[-1].timestamp != decision_timestamp for series in history.values()
        ):
            raise ApprovalError("Portfolio context requires a complete closed-bar batch")
        return PortfolioContext(
            decision_timestamp=decision_timestamp,
            market=manifest.market,
            timeframe=strategy.timeframe,
            history={symbol: tuple(series) for symbol, series in history.items()},
            universe=None,
            universe_version="approval:" + manifest.checksum[:12],
            equity=account.equity,
            holdings={
                canonical_symbol(position.symbol, position.market): position.qty
                for position in account.positions
                if position.market == manifest.market
            },
            prices={symbol: series[-1].close for symbol, series in history.items()},
            benchmark_symbol="BTC/USD" if manifest.market == Market.CRYPTO else "SPY",
        )


@dataclass(frozen=True)
class PortfolioRuntimeResult:
    decision_id: str | None
    target: TargetPortfolio | None
    risk: RiskOverlayDecision | None
    orders: tuple[PortfolioOrderRequest, ...]
    submitted_order_ids: tuple[str, ...]
    status: str


class PortfolioStateRepository:
    def __init__(self, path: str | Path, *, store: SQLiteStore | None = None) -> None:
        self.store = store or SQLiteStore(path)
        self._owns_store = store is None
        self._lock = threading.RLock()
        self._migrate()

    def _migrate(self) -> None:
        with self.store.lock, self.store.connection:
            self.store.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS portfolio_runtime_sessions (
                    session_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                    manifest_checksum TEXT NOT NULL, stage TEXT NOT NULL,
                    started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolio_decisions (
                    decision_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    manifest_checksum TEXT NOT NULL, decision_timestamp TEXT NOT NULL,
                    market TEXT NOT NULL, strategy_name TEXT NOT NULL,
                    target_json TEXT NOT NULL, risk_json TEXT NOT NULL,
                    UNIQUE(manifest_checksum, decision_timestamp, market),
                    FOREIGN KEY(session_id) REFERENCES portfolio_runtime_sessions(session_id)
                );
                CREATE TABLE IF NOT EXISTS portfolio_orders (
                    client_order_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL,
                    symbol TEXT NOT NULL, market TEXT NOT NULL, side TEXT NOT NULL,
                    quantity REAL NOT NULL, reference_price REAL NOT NULL,
                    status TEXT NOT NULL, broker_order_id TEXT NOT NULL DEFAULT '',
                    cumulative_filled_quantity REAL NOT NULL DEFAULT 0,
                    average_fill_price REAL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(decision_id) REFERENCES portfolio_decisions(decision_id)
                );
                CREATE TABLE IF NOT EXISTS portfolio_fills (
                    activity_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL, quantity REAL NOT NULL,
                    price REAL NOT NULL, fee REAL NOT NULL,
                    realized_pnl REAL NOT NULL, unrealized_pnl REAL NOT NULL,
                    slippage REAL NOT NULL,
                    FOREIGN KEY(client_order_id) REFERENCES portfolio_orders(client_order_id)
                );
                CREATE TABLE IF NOT EXISTS portfolio_high_water (
                    account_id TEXT PRIMARY KEY, equity REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            columns = {
                str(row[1])
                for row in self.store.connection.execute("PRAGMA table_info(portfolio_orders)")
            }
            if "cumulative_filled_quantity" not in columns:
                self.store.connection.execute(
                    "ALTER TABLE portfolio_orders ADD COLUMN "
                    "cumulative_filled_quantity REAL NOT NULL DEFAULT 0"
                )
            if "average_fill_price" not in columns:
                self.store.connection.execute(
                    "ALTER TABLE portfolio_orders ADD COLUMN average_fill_price REAL"
                )

    def begin_session(self, account_id: str, manifest_checksum: str, stage: ApprovalStage) -> str:
        session_id = uuid4().hex
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """INSERT INTO portfolio_runtime_sessions(
                    session_id, account_id, manifest_checksum, stage, started_at, status
                ) VALUES (?, ?, ?, ?, ?, 'running')""",
                (
                    session_id,
                    account_id,
                    manifest_checksum,
                    stage.value,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return session_id

    def end_session(self, session_id: str, status: str) -> None:
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """UPDATE portfolio_runtime_sessions
                   SET ended_at = ?, status = ? WHERE session_id = ?""",
                (datetime.now(UTC).isoformat(), status, session_id),
            )

    def has_decision(self, manifest_checksum: str, timestamp: datetime, market: Market) -> bool:
        with self.store.lock:
            row = self.store.connection.execute(
                """SELECT 1 FROM portfolio_decisions
                   WHERE manifest_checksum = ? AND decision_timestamp = ? AND market = ?""",
                (manifest_checksum, timestamp.isoformat(), market.value),
            ).fetchone()
        return row is not None

    def record_decision(
        self,
        session_id: str,
        manifest_checksum: str,
        target: TargetPortfolio,
        risk: RiskOverlayDecision,
        market: Market,
    ) -> str:
        decision_id = _decision_id(manifest_checksum, target.decision_timestamp, market)
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """INSERT OR IGNORE INTO portfolio_decisions(
                    decision_id, session_id, manifest_checksum, decision_timestamp,
                    market, strategy_name, target_json, risk_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id,
                    session_id,
                    manifest_checksum,
                    target.decision_timestamp.isoformat(),
                    market.value,
                    target.strategy_name,
                    json.dumps(target.to_dict(), sort_keys=True, default=str),
                    json.dumps(asdict(risk), sort_keys=True, default=str),
                ),
            )
        return decision_id

    def record_order(self, order: PortfolioOrderRequest, status: str) -> None:
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """INSERT OR IGNORE INTO portfolio_orders(
                    client_order_id, decision_id, symbol, market, side, quantity,
                    reference_price, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order.client_order_id,
                    order.decision_id,
                    order.symbol,
                    order.market.value,
                    order.side.value,
                    order.quantity,
                    order.reference_price,
                    status,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def update_order(self, client_order_id: str, status: str, broker_order_id: str = "") -> None:
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """UPDATE portfolio_orders SET status = ?, broker_order_id = ?
                   WHERE client_order_id = ?""",
                (status, broker_order_id, client_order_id),
            )

    def has_order(self, client_order_id: str) -> bool:
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT 1 FROM portfolio_orders WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        return row is not None

    def apply_trade_update(
        self,
        *,
        client_order_id: str,
        broker_order_id: str,
        status: str,
        cumulative_filled_quantity: float,
        average_fill_price: float | None,
        occurred_at: datetime,
        activity_id: str,
        fee: float,
        realized_pnl: float,
        unrealized_pnl: float,
    ) -> bool:
        with self.store.lock, self.store.connection:
            row = self.store.connection.execute(
                """SELECT cumulative_filled_quantity, reference_price, side
                   FROM portfolio_orders WHERE client_order_id = ?""",
                (client_order_id,),
            ).fetchone()
            if row is None:
                return False
            previous = float(row[0] or 0.0)
            fill_delta = max(0.0, cumulative_filled_quantity - previous)
            reference = float(row[1] or 0.0)
            side = str(row[2])
            self.store.connection.execute(
                """UPDATE portfolio_orders SET status = ?, broker_order_id = ?,
                   cumulative_filled_quantity = ?, average_fill_price = ?
                   WHERE client_order_id = ?""",
                (
                    status,
                    broker_order_id,
                    max(previous, cumulative_filled_quantity),
                    average_fill_price,
                    client_order_id,
                ),
            )
            if fill_delta > 0 and average_fill_price is not None:
                direction = 1.0 if side == "buy" else -1.0
                slippage = (average_fill_price - reference) * fill_delta * direction
                self.store.connection.execute(
                    """INSERT OR IGNORE INTO portfolio_fills(
                        activity_id, client_order_id, occurred_at, quantity, price,
                        fee, realized_pnl, unrealized_pnl, slippage
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        activity_id,
                        client_order_id,
                        occurred_at.isoformat(),
                        fill_delta,
                        average_fill_price,
                        fee,
                        realized_pnl,
                        unrealized_pnl,
                        slippage,
                    ),
                )
        return True

    def record_fill(
        self,
        *,
        activity_id: str,
        client_order_id: str,
        occurred_at: datetime,
        quantity: float,
        price: float,
        fee: float,
        realized_pnl: float,
        unrealized_pnl: float,
        slippage: float,
    ) -> None:
        with self.store.lock, self.store.connection:
            self.store.connection.execute(
                """INSERT OR IGNORE INTO portfolio_fills(
                    activity_id, client_order_id, occurred_at, quantity, price,
                    fee, realized_pnl, unrealized_pnl, slippage
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    activity_id,
                    client_order_id,
                    occurred_at.isoformat(),
                    quantity,
                    price,
                    fee,
                    realized_pnl,
                    unrealized_pnl,
                    slippage,
                ),
            )

    def update_high_water(self, account_id: str, equity: float) -> float:
        with self.store.lock, self.store.connection:
            row = self.store.connection.execute(
                "SELECT equity FROM portfolio_high_water WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            high_water = max(float(row[0]) if row else 0.0, equity)
            self.store.connection.execute(
                """INSERT INTO portfolio_high_water(account_id, equity, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(account_id) DO UPDATE SET
                     equity = excluded.equity, updated_at = excluded.updated_at""",
                (account_id, high_water, datetime.now(UTC).isoformat()),
            )
        return high_water

    def high_water(self, account_id: str) -> float:
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT equity FROM portfolio_high_water WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        return float(row[0]) if row is not None else 0.0

    def close(self) -> None:
        if self._owns_store:
            self.store.acquire()
            self.store.release()


@dataclass(frozen=True)
class RolloutEvidence:
    stage: ApprovalStage
    started_at: datetime
    fills_by_market: dict[str, int]
    duplicate_orders: int = 0
    state_divergences: int = 0
    unresolved_reconciliations: int = 0
    operator_live_approval: bool = False
    live_dollar_cap: float = 0.0


class RolloutController:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, evidence: RolloutEvidence) -> None:
        payload = asdict(evidence)
        payload["stage"] = evidence.stage.value
        payload["started_at"] = evidence.started_at.isoformat()
        atomic_write(self.path, json.dumps(payload, indent=2, sort_keys=True))

    def load(self) -> RolloutEvidence | None:
        if not self.path.exists():
            return None
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return RolloutEvidence(
            stage=ApprovalStage(str(raw["stage"])),
            started_at=datetime.fromisoformat(str(raw["started_at"])),
            fills_by_market={
                str(key): int(value) for key, value in dict(raw.get("fills_by_market", {})).items()
            },
            duplicate_orders=int(raw.get("duplicate_orders", 0)),
            state_divergences=int(raw.get("state_divergences", 0)),
            unresolved_reconciliations=int(raw.get("unresolved_reconciliations", 0)),
            operator_live_approval=bool(raw.get("operator_live_approval", False)),
            live_dollar_cap=float(raw.get("live_dollar_cap", 0.0)),
        )

    def can_advance(self, evidence: RolloutEvidence, now: datetime) -> tuple[bool, str]:
        if any(
            (
                evidence.duplicate_orders,
                evidence.state_divergences,
                evidence.unresolved_reconciliations,
            )
        ):
            return False, "operational_incidents_present"
        elapsed = now - evidence.started_at
        if evidence.stage == ApprovalStage.SHADOW:
            return (elapsed >= timedelta(days=30), "shadow_requires_30_days")
        if evidence.stage == ApprovalStage.PAPER:
            enough_fills = bool(evidence.fills_by_market) and all(
                count >= 50 for count in evidence.fills_by_market.values()
            )
            return (
                elapsed >= timedelta(days=90) and enough_fills,
                "paper_requires_90_days_and_50_fills_per_sleeve",
            )
        if evidence.stage == ApprovalStage.CANARY:
            allowed = evidence.operator_live_approval and evidence.live_dollar_cap > 0
            return allowed, "live_requires_operator_approval_and_positive_cap"
        return False, "live_has_no_automatic_advance"


class PortfolioRuntime:
    def __init__(
        self,
        manifest: StrategyApprovalManifest,
        broker: PortfolioRuntimeBroker,
        repository: PortfolioStateRepository,
        *,
        account_id: str = "unknown",
        allow_live_execution: bool = False,
        live_dollar_cap: float = 0.0,
    ) -> None:
        if not manifest.checksum or manifest.checksum != manifest.calculated_checksum():
            raise ApprovalError("PortfolioRuntime requires a signed approval manifest")
        if manifest.approval_status != "green" or manifest.archived:
            raise ApprovalError("PortfolioRuntime requires an active GREEN manifest")
        if manifest.gate_policy_version != CURRENT_GATE_POLICY_VERSION:
            raise ApprovalError("PortfolioRuntime refuses a stale gate policy")
        if manifest.expires_at <= datetime.now(UTC):
            raise ApprovalError("PortfolioRuntime refuses an expired manifest")
        self.manifest = manifest
        self.strategy: PortfolioStrategy = manifest.instantiate_strategy()
        self.broker = broker
        self.repository = repository
        self.allow_live_execution = allow_live_execution
        self.live_dollar_cap = live_dollar_cap
        self.account_id = account_id
        self.policy = rebalance_policy_for(self.strategy)
        self.decision_service = PortfolioDecisionService()
        self.risk_overlay = PortfolioRiskOverlay()
        self.planner = TargetToOrderPlanner()
        self.history: dict[str, list[Bar]] = {symbol: [] for symbol in manifest.symbols}
        self.previous_timestamp: datetime | None = None
        self.session_id = repository.begin_session(
            account_id, manifest.checksum, manifest.approved_stage
        )

    def seed(self, bars: list[Bar]) -> None:
        """Seed only closed, approved bars without producing a decision."""
        for bar in sorted(bars, key=lambda item: (item.timestamp, item.symbol)):
            symbol = canonical_symbol(bar.symbol, bar.market)
            if (
                bar.market != self.manifest.market
                or bar.timeframe != self.strategy.timeframe
                or symbol not in self.history
            ):
                continue
            series = self.history[symbol]
            if not series or series[-1].timestamp < bar.timestamp:
                series.append(bar)
        timestamps = {series[-1].timestamp for series in self.history.values() if series}
        if len(timestamps) == 1:
            self.previous_timestamp = next(iter(timestamps))

    def process_closed_bars(self, bars: list[Bar]) -> PortfolioRuntimeResult:
        eligible = [
            bar
            for bar in bars
            if bar.market == self.manifest.market
            and bar.timeframe == self.strategy.timeframe
            and canonical_symbol(bar.symbol, bar.market) in self.history
        ]
        if not eligible:
            return PortfolioRuntimeResult(None, None, None, (), (), "no_eligible_bars")
        timestamp = max(bar.timestamp for bar in eligible)
        batch_symbols = {
            canonical_symbol(bar.symbol, bar.market)
            for bar in eligible
            if bar.timestamp == timestamp
        }
        if batch_symbols != set(self.manifest.symbols):
            return PortfolioRuntimeResult(None, None, None, (), (), "incomplete_batch")
        for bar in sorted(eligible, key=lambda item: (item.timestamp, item.symbol)):
            symbol = canonical_symbol(bar.symbol, bar.market)
            series = self.history[symbol]
            if not series or series[-1].timestamp < bar.timestamp:
                series.append(bar)
        scheduled = self.policy.is_period_start(self.previous_timestamp, timestamp)
        self.previous_timestamp = timestamp
        if self.repository.has_decision(self.manifest.checksum, timestamp, self.manifest.market):
            return PortfolioRuntimeResult(None, None, None, (), (), "duplicate_decision")
        if any(
            len(self.history[symbol]) < self.strategy.minimum_history_bars
            for symbol in self.manifest.symbols
        ):
            return PortfolioRuntimeResult(None, None, None, (), (), "warming_up")
        account = self.broker.account()
        persisted_high_water = self.repository.high_water(self.account_id)
        account = replace(
            account,
            peak_equity=max(account.peak_equity, persisted_high_water, account.equity),
        )
        self.repository.update_high_water(self.account_id, account.equity)
        context = self.decision_service.build_context(
            self.manifest,
            self.strategy,
            self.history,
            account,
            timestamp,
        )
        if not scheduled and not self.strategy.requires_intermediate_risk_rebalance(context):
            return PortfolioRuntimeResult(None, None, None, (), (), "not_scheduled")
        raw_target = self.strategy.decide(context)
        if not scheduled:
            current_weights = {
                canonical_symbol(position.symbol, position.market): (
                    position.market_value / account.equity
                )
                for position in account.positions
                if position.market == self.manifest.market and not position.is_flat
            }
            reducing_weights = tuple(
                TargetWeight(
                    weight.symbol,
                    min(weight.weight, current_weights.get(weight.symbol, 0.0)),
                    "intermediate_risk_reduction_only",
                )
                for weight in raw_target.weights
                if min(weight.weight, current_weights.get(weight.symbol, 0.0)) > 0
            )
            raw_target = replace(
                raw_target,
                weights=reducing_weights,
                cash_weight=1.0 - sum(weight.weight for weight in reducing_weights),
                metadata={**raw_target.metadata, "intermediate_risk_reduction": True},
            )
        if any(
            canonical_symbol(weight.symbol, self.manifest.market) not in self.manifest.symbols
            for weight in raw_target.weights
        ):
            raise ApprovalError("Strategy emitted a symbol outside the approved universe")
        maximum_allocation = self.manifest.maximum_allocation
        if self.manifest.approved_stage in {
            ApprovalStage.CANARY,
            ApprovalStage.LIVE,
        }:
            if not self.allow_live_execution or self.live_dollar_cap <= 0:
                raise ApprovalError("Canary/live execution is disabled without operator cap")
            maximum_allocation = min(
                maximum_allocation, self.live_dollar_cap / max(1.0, account.equity)
            )
        risk = self.risk_overlay.apply(
            raw_target,
            account,
            market=self.manifest.market,
            approved_symbols=self.manifest.symbols,
            maximum_allocation=maximum_allocation,
            stage=self.manifest.approved_stage,
        )
        rules = {
            symbol: self.broker.asset_trading_rules(symbol, self.manifest.market)
            for symbol in self.manifest.symbols
        }
        planned = self.planner.plan(
            risk.target,
            account,
            market=self.manifest.market,
            prices=context.prices,
            rules=rules,
            approved_symbols=self.manifest.symbols,
            open_order_symbols=self.broker.open_portfolio_order_symbols(),
        )
        decision_id = self.repository.record_decision(
            self.session_id,
            self.manifest.checksum,
            risk.target,
            risk,
            self.manifest.market,
        )
        orders = tuple(
            PortfolioOrderRequest(**{**asdict(order), "decision_id": decision_id})
            for order in planned
        )
        submitted: list[str] = []
        for order in orders:
            if self.manifest.approved_stage == ApprovalStage.SHADOW:
                self.repository.record_order(order, "shadow_planned")
                continue
            self.repository.record_order(order, "submitting")
            try:
                broker_order_id = self.broker.submit_portfolio_order(order)
            except Exception:
                self.repository.update_order(order.client_order_id, "reconciliation_required")
                raise
            self.repository.update_order(order.client_order_id, "submitted", broker_order_id)
            submitted.append(broker_order_id)
        return PortfolioRuntimeResult(
            decision_id,
            risk.target,
            risk,
            orders,
            tuple(submitted),
            "shadow_planned"
            if self.manifest.approved_stage == ApprovalStage.SHADOW
            else "submitted",
        )

    def close(self, status: str = "stopped") -> None:
        self.repository.end_session(self.session_id, status)


def _decision_id(checksum: str, timestamp: datetime, market: Market) -> str:
    import hashlib

    return hashlib.sha256(
        f"{checksum}|{timestamp.isoformat()}|{market.value}".encode()
    ).hexdigest()[:24]


__all__ = [
    "PortfolioRuntime",
    "PortfolioRuntimeBroker",
    "PortfolioRuntimeResult",
    "PortfolioDecisionService",
    "PortfolioStateRepository",
    "RolloutController",
    "RolloutEvidence",
]
