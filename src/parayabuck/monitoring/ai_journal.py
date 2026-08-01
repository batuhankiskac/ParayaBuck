from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from parayabuck.core.models import Bar, Direction, Fill, OrderIntent, OrderState, Signal
from parayabuck.logger import get_logger
from parayabuck.markets.regime import RegimeAssessment
from parayabuck.strategies.base import StrategyEvaluation

log = get_logger("parayabuck.monitoring.ai_journal")

SCHEMA_VERSION = 1


@dataclass
class PredictionRecord:
    signal_id: str
    symbol: str
    timeframe: str
    strategy: str
    direction: str
    entry: float
    stop: float
    target: float
    score: float
    confidence: float
    registered_at: str
    horizon_bars: int
    decision_file: str
    traded: bool
    bars_elapsed: int = 0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    closest_target_pct: float | None = None
    closest_stop_pct: float | None = None
    client_order_id: str | None = None


@dataclass(frozen=True)
class PredictionOutcome:
    signal_id: str
    resolved_at: str
    outcome: str
    verdict: str
    actual_price: float
    return_pct: float
    bars_elapsed: int
    mfe_pct: float
    mae_pct: float
    closest_target_pct: float | None
    closest_stop_pct: float | None
    traded: bool
    explanation: str


class AIDecisionJournal:
    """Append-only, AI-readable decision artifacts and forward outcome tracking."""

    def __init__(
        self,
        base_path: str,
        mode: str,
        timeframe: str,
        horizon_bars: int = 48,
        run_id: str | None = None,
    ) -> None:
        self.mode = mode
        self.timeframe = timeframe
        self.horizon_bars = horizon_bars
        self.run_id = run_id or f"{mode}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
        base = Path(base_path)
        self.root = base / mode
        if mode == "backtest":
            self.root /= self.run_id
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._open_path = self.root / "open_predictions.json"
        self._stats_path = self.root / "strategy_stats.json"
        self._open: dict[str, PredictionRecord] = {}
        self._stats: dict[str, dict[str, Any]] = {}
        self._pending_entry_fills: dict[str, list[Fill]] = {}
        self._load_state()
        self._write_manifest()

    def observe_bar(self, bar: Bar) -> tuple[PredictionOutcome, ...]:
        """Advance matching predictions using only bars after the signal bar."""
        resolved: list[PredictionOutcome] = []
        with self._lock:
            for record in list(self._open.values()):
                if record.symbol != bar.symbol or record.timeframe != bar.timeframe:
                    continue
                registered_at = datetime.fromisoformat(record.registered_at)
                if bar.timestamp <= registered_at:
                    continue
                record.bars_elapsed += 1
                favorable, adverse = self._excursions(record, bar)
                record.mfe_pct = max(record.mfe_pct, favorable)
                record.mae_pct = min(record.mae_pct, adverse)
                record.closest_target_pct = self._closest(
                    record.closest_target_pct,
                    abs(record.target - bar.close) / record.entry * 100,
                )
                record.closest_stop_pct = self._closest(
                    record.closest_stop_pct,
                    abs(record.stop - bar.close) / record.entry * 100,
                )
                outcome = self._resolve(record, bar)
                if outcome is not None:
                    resolved.append(outcome)
                    del self._open[record.signal_id]
                    self._write_outcome(record, outcome)
                    self._update_stats(record, outcome)
            self._atomic_json(self._open_path, [asdict(item) for item in self._open.values()])
        return tuple(resolved)

    def record_scan(
        self,
        bar: Bar,
        assessment: RegimeAssessment,
        position: str,
        evaluations: tuple[StrategyEvaluation, ...],
    ) -> None:
        day = self._day_dir(bar.timestamp)
        digest = day / "scan-digest.md"
        if not digest.exists():
            self._append(digest, f"# Scan digest {bar.timestamp.date().isoformat()}\n\n")
        summaries = " | ".join(
            f"{item.strategy}: {'FIRED' if item.fired else 'skipped'} ({item.summary})"
            for item in evaluations
        )
        line = (
            f"{bar.timestamp:%H:%M:%S} | {bar.symbol} {bar.timeframe} | "
            f"close={bar.close:.8g} | regime={assessment.regime.value} "
            f"({', '.join(assessment.reasons)}) | position={position} | {summaries}\n"
        )
        with self._lock:
            self._append(digest, line)
            self._event(
                bar.timestamp,
                "scan",
                {
                    "symbol": bar.symbol,
                    "timeframe": bar.timeframe,
                    "market": bar.market.value,
                    "bar": self._bar_payload(bar),
                    "regime": self._regime_payload(assessment),
                    "position": position,
                    "strategies": [self._evaluation_payload(item) for item in evaluations],
                },
            )

    def record_signal(
        self,
        bar: Bar,
        signal: Signal,
        evaluation: StrategyEvaluation,
        assessment: RegimeAssessment,
        higher_regime: str,
        benchmark_regime: str,
        gate_trail: list[dict[str, Any]],
        traded: bool,
        order: OrderIntent | None = None,
        order_state: OrderState | None = None,
        fill: Fill | None = None,
        risk_diagnostics: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            pending_fills = (
                self._pending_entry_fills.pop(order.client_order_id, [])
                if order is not None
                else []
            )
            if pending_fills:
                traded = True
                if fill is None:
                    fill = pending_fills[-1]
            day = self._day_dir(signal.timestamp)
            safe_symbol = signal.symbol.replace("/", "-")
            filename = (
                f"{signal.timestamp:%H%M%S}_{safe_symbol}_{signal.strategy}_"
                f"{signal.signal_id[:8]}.md"
            )
            path = day / filename
            relative = str(path.relative_to(self.root))
            payload = {
                "signal_id": signal.signal_id,
                "symbol": signal.symbol,
                "timeframe": bar.timeframe,
                "market": signal.market.value,
                "strategy": signal.strategy,
                "direction": signal.direction.value,
                "score": signal.score,
                "confidence": signal.confidence,
                "reasons": list(signal.reasons),
                "market_context": {
                    "bar": self._bar_payload(bar),
                    "regime": self._regime_payload(assessment),
                    "higher_timeframe_regime": higher_regime,
                    "benchmark_regime": benchmark_regime,
                },
                "strategy_evaluation": self._evaluation_payload(evaluation),
                "prediction": {
                    "entry": signal.entry_price,
                    "stop": signal.invalidation_price,
                    "target": signal.target_price,
                    "risk_reward": signal.risk_reward,
                    "horizon_bars": self.horizon_bars,
                },
                "gate_trail": gate_trail,
                "execution": {
                    "traded": traded,
                    "order": self._order_payload(order),
                    "order_state": order_state.value if order_state else None,
                    "fill": self._fill_payload(fill),
                },
                "risk_diagnostics": risk_diagnostics or {},
                "decision_file": relative,
            }
            self._write_decision_markdown(path, payload)
            self._event(signal.timestamp, "decision", payload)
            self._open[signal.signal_id] = PredictionRecord(
                signal.signal_id,
                signal.symbol,
                bar.timeframe,
                signal.strategy,
                signal.direction.value,
                signal.entry_price,
                signal.invalidation_price,
                signal.target_price,
                signal.score,
                signal.confidence,
                signal.timestamp.isoformat(),
                self.horizon_bars,
                relative,
                traded,
                client_order_id=order.client_order_id if order is not None else None,
            )
            self._atomic_json(self._open_path, [asdict(item) for item in self._open.values()])
            for pending_fill in pending_fills:
                self._append(
                    path,
                    (
                        f"\n- Async ENTRY fill at "
                        f"{pending_fill.timestamp.astimezone(UTC).isoformat()}: "
                        f"qty={pending_fill.qty}, price={pending_fill.price}, "
                        f"commission={pending_fill.commission}.\n"
                    ),
                )

    def record_broker_fill(self, fill: Fill) -> None:
        """Attach asynchronous/backtest fills to the matching decision artifact."""
        with self._lock:
            record = next(
                (
                    item
                    for item in self._open.values()
                    if item.client_order_id == fill.client_order_id
                ),
                None,
            )
            payload = self._fill_payload(fill)
            self._event(
                fill.timestamp,
                "fill",
                {
                    "signal_id": record.signal_id if record else None,
                    "symbol": fill.symbol,
                    "fill": payload,
                },
            )
            if record is not None:
                label = "EXIT" if fill.direction == Direction.FLAT else "ENTRY"
                if label == "ENTRY":
                    record.traded = True
                    self._atomic_json(
                        self._open_path,
                        [asdict(item) for item in self._open.values()],
                    )
                self._append(
                    self.root / record.decision_file,
                    (
                        f"\n- Async {label} fill at {fill.timestamp.astimezone(UTC).isoformat()}: "
                        f"qty={fill.qty}, price={fill.price}, commission={fill.commission}.\n"
                    ),
                )
            elif fill.direction != Direction.FLAT:
                self._pending_entry_fills.setdefault(fill.client_order_id, []).append(fill)

    def record_account(self, timestamp: datetime, payload: dict[str, Any]) -> None:
        with self._lock:
            self._event(timestamp, "account", payload)

    def _resolve(self, record: PredictionRecord, bar: Bar) -> PredictionOutcome | None:
        is_long = record.direction == Direction.LONG.value
        stop_hit = bar.low <= record.stop if is_long else bar.high >= record.stop
        target_hit = bar.high >= record.target if is_long else bar.low <= record.target
        actual_price: float
        outcome: str
        verdict: str
        if stop_hit:
            actual_price, outcome, verdict = record.stop, "stop_hit", "wrong"
            explanation = "Price hit the invalidation level; the setup failed."
        elif target_hit:
            actual_price, outcome, verdict = record.target, "target_hit", "correct"
            explanation = "Price reached the predicted target before invalidation."
        elif record.bars_elapsed >= record.horizon_bars:
            actual_price = bar.close
            signed_return = self._signed_return(record, actual_price)
            verdict = "correct" if signed_return > 0 else "wrong"
            outcome = f"timeout_{verdict}"
            explanation = (
                "The horizon expired with price moving in the predicted direction."
                if verdict == "correct"
                else "The horizon expired without a favorable directional return."
            )
        else:
            return None
        return_pct = self._signed_return(record, actual_price)
        return PredictionOutcome(
            record.signal_id,
            bar.timestamp.isoformat(),
            outcome,
            verdict,
            actual_price,
            return_pct,
            record.bars_elapsed,
            record.mfe_pct,
            record.mae_pct,
            record.closest_target_pct,
            record.closest_stop_pct,
            record.traded,
            explanation,
        )

    def _write_decision_markdown(self, path: Path, payload: dict[str, Any]) -> None:
        evaluation = payload["strategy_evaluation"]
        prediction = payload["prediction"]
        context = payload["market_context"]
        execution = payload["execution"]
        lines = [
            (
                f"# Decision: {payload['direction'].upper()} "
                f"{payload['symbol']} — {payload['strategy']}"
            ),
            "",
            f"- Time: {context['bar']['timestamp']}",
            f"- Signal ID: `{payload['signal_id']}`",
            f"- Score: {payload['score']:.2f}/100 | Confidence: {payload['confidence']:.3f}",
            (
                "- Execution: TRADED"
                if execution["traded"]
                else (
                    "- Execution: WORKING — awaiting broker fill"
                    if execution["order_state"] in {
                        OrderState.ACCEPTED.value,
                        OrderState.PARTIALLY_FILLED.value,
                        OrderState.RECONCILIATION_REQUIRED.value,
                        OrderState.UNKNOWN.value,
                    }
                    else "- Execution: VIRTUAL — not traded"
                )
            ),
            "",
            "## Market context",
            "",
            f"- Close: {context['bar']['close']:.8g}; timeframe: {payload['timeframe']}.",
            (
                f"- Regime: {context['regime']['regime']} "
                f"(confidence {context['regime']['confidence']:.3f})."
            ),
            f"- Regime evidence: {', '.join(context['regime']['reasons'])}.",
            f"- Higher timeframe regime: {context['higher_timeframe_regime']}.",
            f"- Benchmark regime: {context['benchmark_regime']}.",
            "",
            "## Strategy evaluation",
            "",
            f"{evaluation['summary']}",
            "",
        ]
        for item in evaluation["conditions"]:
            state = "PASS" if item["passed"] else "FAIL"
            lines.append(
                f"- **{state} `{item['code']}`** — actual: `{self._compact(item['actual'])}`; "
                f"expected: {item['expected']}. {item['explanation']}"
            )
        if evaluation["metrics"]:
            lines.extend(["", f"- Metrics: `{self._compact(evaluation['metrics'])}`"])
        lines.extend(
            [
                "",
                "## Prediction",
                "",
                f"- Direction: {payload['direction'].upper()} from {prediction['entry']:.8g}.",
                f"- Invalidation (stop): {prediction['stop']:.8g}.",
                f"- Target: {prediction['target']:.8g}.",
                f"- Risk/reward: {prediction['risk_reward']:.3f}.",
                f"- Expected horizon: {prediction['horizon_bars']} bars.",
                "",
                "## Gate trail",
                "",
            ]
        )
        for index, gate in enumerate(payload["gate_trail"], 1):
            lines.append(
                f"{index}. **{gate['gate']} — {gate['status'].upper()}**: {gate['reason']}"
            )
        lines.extend(["", "## Order/fill lifecycle", ""])
        if execution["order"] is None:
            lines.append("- No order was submitted.")
        else:
            order = execution["order"]
            lines.append(
                f"- Order `{order['client_order_id']}`: qty={order['qty']}, "
                f"entry={order['entry']}, stop={order['stop']}, target={order['target']}."
            )
            lines.append(f"- Broker state: {execution['order_state']}.")
            if execution["fill"]:
                fill = execution["fill"]
                lines.append(
                    f"- Fill: qty={fill['qty']} at {fill['price']} "
                    f"(commission={fill['commission']})."
                )
        lines.extend(
            [
                "",
                "_Outcome tracking is open. A Prediction vs. Actual section will be "
                "appended after target, stop, or timeout resolution._",
                "",
            ]
        )
        self._atomic_text(path, "\n".join(lines))

    def _write_outcome(self, record: PredictionRecord, outcome: PredictionOutcome) -> None:
        path = self.root / record.decision_file
        comparison = [
            "",
            "## Prediction vs. Actual",
            "",
            f"- **Verdict: {outcome.verdict.upper()} ({outcome.outcome})**",
            f"- Predicted: {record.direction.upper()} from {record.entry:.8g}, "
            f"target {record.target:.8g}, stop {record.stop:.8g}, "
            f"within {record.horizon_bars} bars.",
            f"- Actual: resolved at {outcome.actual_price:.8g} after "
            f"{outcome.bars_elapsed} bars; direction-adjusted return "
            f"{outcome.return_pct:+.3f}%.",
            f"- Path: MFE {outcome.mfe_pct:+.3f}%, MAE {outcome.mae_pct:+.3f}%.",
            (
                "- Closest close-distance: "
                f"target {outcome.closest_target_pct:.3f}%, "
                f"stop {outcome.closest_stop_pct:.3f}%."
                if outcome.closest_target_pct is not None and outcome.closest_stop_pct is not None
                else "- Closest close-distance: unavailable."
            ),
            f"- Execution class: {'traded' if outcome.traded else 'virtual'}.",
            f"- Reading: {outcome.explanation}",
            "",
        ]
        self._append(path, "\n".join(comparison))
        resolved_at = datetime.fromisoformat(outcome.resolved_at)
        day = self._day_dir(resolved_at)
        outcomes_path = day / "outcomes.md"
        if not outcomes_path.exists():
            self._append(outcomes_path, f"# Outcomes {resolved_at.date().isoformat()}\n\n")
        self._append(
            outcomes_path,
            f"{resolved_at:%H:%M:%S} | {record.symbol} {record.strategy} | "
            f"{outcome.outcome} after {outcome.bars_elapsed} bars | "
            f"return {outcome.return_pct:+.3f}% | {outcome.verdict.upper()} | "
            f"{'traded' if outcome.traded else 'virtual'} | file: {record.decision_file}\n",
        )
        self._event(
            resolved_at,
            "outcome",
            {
                **asdict(outcome),
                "symbol": record.symbol,
                "timeframe": record.timeframe,
                "strategy": record.strategy,
                "prediction": {
                    "direction": record.direction,
                    "entry": record.entry,
                    "stop": record.stop,
                    "target": record.target,
                    "horizon_bars": record.horizon_bars,
                },
                "decision_file": record.decision_file,
            },
        )

    def _update_stats(self, record: PredictionRecord, outcome: PredictionOutcome) -> None:
        stats = self._stats.setdefault(
            record.strategy,
            {
                "resolved": 0,
                "correct": 0,
                "wrong": 0,
                "target_hit": 0,
                "stop_hit": 0,
                "timeout_correct": 0,
                "timeout_wrong": 0,
                "traded_resolved": 0,
                "virtual_resolved": 0,
                "total_return_pct": 0.0,
            },
        )
        stats["resolved"] += 1
        stats[outcome.verdict] = stats.get(outcome.verdict, 0) + 1
        stats[outcome.outcome] = stats.get(outcome.outcome, 0) + 1
        stats["traded_resolved" if record.traded else "virtual_resolved"] += 1
        stats["total_return_pct"] += outcome.return_pct
        stats["accuracy"] = stats["correct"] / stats["resolved"]
        stats["avg_return_pct"] = stats["total_return_pct"] / stats["resolved"]
        stats["updated_at"] = outcome.resolved_at
        self._atomic_json(self._stats_path, self._stats)

    def _load_state(self) -> None:
        raw_open = self._load_json(self._open_path, [])
        if isinstance(raw_open, list):
            for item in raw_open:
                record = PredictionRecord(**item)
                self._open[record.signal_id] = record
        raw_stats = self._load_json(self._stats_path, {})
        if isinstance(raw_stats, dict):
            self._stats = raw_stats

    def _load_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            quarantine = path.with_name(f"{path.name}.corrupt-{datetime.now(UTC):%Y%m%dT%H%M%SZ}")
            try:
                path.replace(quarantine)
            except OSError:
                pass
            log.error("AI journal state quarantined: path=%s error=%s", path, exc)
            return default

    def _write_manifest(self) -> None:
        self._atomic_json(
            self.root / "manifest.json",
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "mode": self.mode,
                "timeframe": self.timeframe,
                "prediction_horizon_bars": self.horizon_bars,
                "timestamps": "UTC ISO-8601",
                "files": {
                    "events.jsonl": (
                        "Append-only machine-readable scan, decision, account, and outcome events."
                    ),
                    "scan-digest.md": (
                        "One-line strategy elimination summaries for every processed bar."
                    ),
                    "outcomes.md": "Resolved prediction index for the UTC day.",
                    "decision markdown": (
                        "Evidence, gate trail, execution, and eventual prediction comparison."
                    ),
                    "open_predictions.json": "Restart-safe unresolved prediction state.",
                    "strategy_stats.json": "Aggregate correct/wrong and traded/virtual results.",
                },
            },
        )

    def _event(self, timestamp: datetime, event_type: str, payload: dict[str, Any]) -> None:
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "event_type": event_type,
            "run_id": self.run_id,
            "timestamp": timestamp.astimezone(UTC).isoformat(),
            **payload,
        }
        path = self._day_dir(timestamp) / "events.jsonl"
        self._append(path, json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _day_dir(self, timestamp: datetime) -> Path:
        path = self.root / timestamp.astimezone(UTC).date().isoformat()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _atomic_json(self, path: Path, payload: Any) -> None:
        self._atomic_text(
            path,
            json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False),
        )

    @staticmethod
    def _atomic_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _append(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)

    @staticmethod
    def _closest(current: float | None, candidate: float) -> float:
        return candidate if current is None else min(current, candidate)

    @staticmethod
    def _signed_return(record: PredictionRecord, price: float) -> float:
        raw = (price - record.entry) / record.entry * 100
        return raw if record.direction == Direction.LONG.value else -raw

    @classmethod
    def _excursions(cls, record: PredictionRecord, bar: Bar) -> tuple[float, float]:
        if record.direction == Direction.LONG.value:
            return (
                (bar.high - record.entry) / record.entry * 100,
                (bar.low - record.entry) / record.entry * 100,
            )
        return (
            (record.entry - bar.low) / record.entry * 100,
            (record.entry - bar.high) / record.entry * 100,
        )

    @staticmethod
    def _bar_payload(bar: Bar) -> dict[str, Any]:
        return {
            "timestamp": bar.timestamp.astimezone(UTC).isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }

    @staticmethod
    def _regime_payload(assessment: RegimeAssessment) -> dict[str, Any]:
        return {
            "regime": assessment.regime.value,
            "confidence": assessment.confidence,
            "trend_strength": assessment.trend_strength,
            "volatility_pct": assessment.volatility_pct,
            "reasons": list(assessment.reasons),
        }

    @staticmethod
    def _evaluation_payload(evaluation: StrategyEvaluation) -> dict[str, Any]:
        return {
            "strategy": evaluation.strategy,
            "fired": evaluation.fired,
            "summary": evaluation.summary,
            "conditions": [asdict(item) for item in evaluation.conditions],
            "metrics": evaluation.metrics or {},
        }

    @staticmethod
    def _order_payload(order: OrderIntent | None) -> dict[str, Any] | None:
        if order is None:
            return None
        return {
            "client_order_id": order.client_order_id,
            "qty": order.qty,
            "entry": order.entry_price,
            "stop": order.stop_price,
            "target": order.target_price,
            "created_at": order.created_at.astimezone(UTC).isoformat(),
        }

    @staticmethod
    def _fill_payload(fill: Fill | None) -> dict[str, Any] | None:
        if fill is None:
            return None
        return {
            "client_order_id": fill.client_order_id,
            "qty": fill.qty,
            "price": fill.price,
            "direction": fill.direction.value,
            "timestamp": fill.timestamp.astimezone(UTC).isoformat(),
            "commission": fill.commission,
        }

    @staticmethod
    def _compact(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


__all__ = ["AIDecisionJournal", "PredictionOutcome", "PredictionRecord"]
