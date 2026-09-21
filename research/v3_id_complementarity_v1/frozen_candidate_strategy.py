"""Research-only implementation of the frozen information-discreteness alpha.

This module intentionally lives under the economic research branch. It is not
registered in the production strategy factory and cannot alter PAPER/LIVE.
"""

from __future__ import annotations

import math

from parayabuck.core.models import Bar, Market
from parayabuck.markets.symbols import canonical_symbol
from parayabuck.portfolio.base import PortfolioContext, PortfolioStrategy
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

FORMATION_RETURNS = 252
SKIP_RETURNS = 21
REQUIRED_CLOSES = FORMATION_RETURNS + SKIP_RETURNS + 1
TOP_N = 6
MAX_ALLOCATION = 0.55
MAX_SINGLE_WEIGHT = 0.10


def _iso_list(bars: tuple[Bar, ...]) -> list[str]:
    return [bar.timestamp.isoformat() for bar in bars]


class EquityInformationDiscretenessContinuous1dV1(PortfolioStrategy):
    """Frozen long-only low-ID continuity sort for positive 12-1 winners."""

    name = "equity_information_discreteness_continuous_v1"
    version = "1.0.0"
    market = Market.EQUITY
    timeframe = "1d"
    display_name = "Information Discreteness Continuous v1"
    supported_timeframes = ("1d",)
    rebalance_policy_declared = "monthly_last_completed_session"

    def __init__(self, parameters: dict[str, object] | None = None) -> None:
        self.audit_events: list[dict[str, object]] = []
        super().__init__(parameters)

    @property
    def minimum_history_bars(self) -> int:
        return REQUIRED_CLOSES

    def _validate_parameters(self) -> None:
        expected = {
            "formation_bars": FORMATION_RETURNS,
            "skip_bars": SKIP_RETURNS,
            "top_n": TOP_N,
            "max_single_weight": MAX_SINGLE_WEIGHT,
            "maximum_allocation": MAX_ALLOCATION,
        }
        unknown = set(self.parameters) - set(expected)
        if unknown:
            raise ValueError(f"Unsupported information-discreteness parameters: {sorted(unknown)}")
        for key, value in self.parameters.items():
            if isinstance(expected[key], int):
                if not isinstance(value, (int, float)) or int(value) != expected[key]:
                    raise ValueError(f"{key} is frozen at {expected[key]}")
            elif not isinstance(value, (int, float)) or not math.isclose(float(value), float(expected[key])):
                raise ValueError(f"{key} is frozen at {expected[key]}")

    @staticmethod
    def _benchmark_window(context: PortfolioContext) -> tuple[Bar, ...]:
        benchmark = canonical_symbol(context.benchmark_symbol, Market.EQUITY)
        bars = tuple(context.history.get(benchmark, ()))
        return bars[-REQUIRED_CLOSES:] if len(bars) >= REQUIRED_CLOSES else ()

    @staticmethod
    def _score_one(
        symbol: str,
        bars: tuple[Bar, ...],
        benchmark_window: tuple[Bar, ...],
    ) -> dict[str, object]:
        record: dict[str, object] = {
            "symbol": symbol,
            "scoreable": False,
            "qualification_status": "unscoreable",
            "rank": None,
        }
        if len(bars) < REQUIRED_CLOSES:
            record["failure_reason"] = "insufficient_exact_history"
            return record
        window = bars[-REQUIRED_CLOSES:]
        if len(benchmark_window) != REQUIRED_CLOSES:
            record["failure_reason"] = "benchmark_session_context_unavailable"
            return record
        timestamps = tuple(bar.timestamp for bar in window)
        benchmark_timestamps = tuple(bar.timestamp for bar in benchmark_window)
        if timestamps != benchmark_timestamps:
            record["failure_reason"] = "missing_or_misaligned_required_session"
            return record
        closes = tuple(float(bar.close) for bar in window)
        if not all(math.isfinite(close) and close > 0.0 for close in closes):
            record["failure_reason"] = "nonfinite_or_nonpositive_price"
            return record

        formation_bars = window[: FORMATION_RETURNS + 1]
        skipped_bars = window[FORMATION_RETURNS + 1 :]
        returns = tuple(closes[index] / closes[index - 1] - 1.0 for index in range(1, FORMATION_RETURNS + 1))
        pret = closes[FORMATION_RETURNS] / closes[0] - 1.0
        positive_count = sum(1 for value in returns if value > 0.0)
        negative_count = sum(1 for value in returns if value < 0.0)
        zero_count = sum(1 for value in returns if value == 0.0)
        denominator = len(returns)
        sign_pret = 1 if pret > 0.0 else (-1 if pret < 0.0 else 0)
        identifier = sign_pret * (negative_count / denominator - positive_count / denominator)
        record.update(
            {
                "scoreable": True,
                "qualification_status": "qualified_positive_pret" if pret > 0.0 else "failed_positive_pret_gate",
                "formation_anchor_session": window[0].timestamp.isoformat(),
                "formation_endpoint_session": formation_bars[-1].timestamp.isoformat(),
                "formation_sessions": _iso_list(formation_bars[1:]),
                "skipped_sessions": _iso_list(skipped_bars),
                "pret": pret,
                "positive_count": positive_count,
                "negative_count": negative_count,
                "zero_count": zero_count,
                "denominator": denominator,
                "positive_share": positive_count / denominator,
                "negative_share": negative_count / denominator,
                "sign_pret": sign_pret,
                "id": identifier,
            }
        )
        return record

    def _score_candidates(
        self,
        context: PortfolioContext,
    ) -> tuple[tuple[tuple[float, str], ...], list[dict[str, object]]]:
        benchmark_window = self._benchmark_window(context)
        records: list[dict[str, object]] = []
        qualified: list[tuple[float, str]] = []
        benchmark = canonical_symbol(context.benchmark_symbol, Market.EQUITY)
        for symbol in sorted(context.history):
            canonical = canonical_symbol(symbol, Market.EQUITY)
            if canonical == benchmark:
                continue
            if not context.in_universe(canonical):
                continue
            if not context.is_ordinary_alpha_eligible(canonical):
                records.append(
                    {
                        "symbol": canonical,
                        "scoreable": False,
                        "qualification_status": "reserved_role_excluded",
                        "rank": None,
                    }
                )
                continue
            record = self._score_one(canonical, tuple(context.history[symbol]), benchmark_window)
            records.append(record)
            if record.get("scoreable") and record.get("qualification_status") == "qualified_positive_pret":
                identifier = record.get("id")
                if not isinstance(identifier, (int, float)) or not math.isfinite(float(identifier)):
                    raise ValueError(f"Nonfinite ID for scoreable symbol {canonical}")
                qualified.append((float(identifier), canonical))
        qualified.sort(key=lambda item: (item[0], item[1]))
        rank_by_symbol = {symbol: rank for rank, (_identifier, symbol) in enumerate(qualified, start=1)}
        for record in records:
            symbol = record.get("symbol")
            if isinstance(symbol, str) and symbol in rank_by_symbol:
                record["rank"] = rank_by_symbol[symbol]
        return tuple(qualified), records

    def rank_candidates(self, context: PortfolioContext) -> tuple[str, ...]:
        ranked, _records = self._score_candidates(context)
        return tuple(symbol for _identifier, symbol in ranked)

    def decide(self, context: PortfolioContext) -> TargetPortfolio:
        ranked, records = self._score_candidates(context)
        selected = tuple(symbol for _identifier, symbol in ranked[:TOP_N])
        event: dict[str, object] = {
            "decision_timestamp": context.decision_timestamp.isoformat(),
            "data_timestamp": context.decision_timestamp.isoformat(),
            "scoreable_pool_count": sum(bool(record.get("scoreable")) for record in records),
            "qualified_positive_pret_count": len(ranked),
            "selected_symbols": list(selected),
            "records": records,
        }
        self.audit_events.append(event)
        if not selected:
            return TargetPortfolio(
                strategy_name=self.name,
                strategy_version=self.version,
                decision_timestamp=context.decision_timestamp,
                data_timestamp=context.decision_timestamp,
                weights=(),
                cash_weight=1.0,
                rebalance_reason=RebalanceReason.RISK_OFF,
                universe_version=context.universe_version,
                universe_snapshot_id=context.universe_version,
                parameters=self.parameter_tuple(),
                metadata={
                    "qualified_symbols": 0,
                    "selected_symbols": (),
                    "hypothesis": "positive 12-1 winners sorted by lower information discreteness",
                    "id_signal_records": records,
                },
            )
        target_weight = min(MAX_ALLOCATION / len(selected), MAX_SINGLE_WEIGHT)
        rank_by_symbol = {symbol: rank for rank, (_identifier, symbol) in enumerate(ranked, start=1)}
        targets = tuple(
            TargetWeight(
                symbol=symbol,
                weight=target_weight,
                reason="information_discreteness_continuous",
                rank=rank_by_symbol[symbol],
            )
            for symbol in sorted(selected)
        )
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=max(0.0, 1.0 - target_weight * len(targets)),
            rebalance_reason=RebalanceReason.SCHEDULED,
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={
                "qualified_symbols": len(ranked),
                "selected_symbols": selected,
                "hypothesis": "positive 12-1 winners sorted by lower information discreteness",
                "id_signal_records": records,
            },
        )

    def audit_payload(self) -> list[dict[str, object]]:
        """Return JSON-ready, immutable-by-convention signal audit events."""
        return self.audit_events


__all__ = [
    "EquityInformationDiscretenessContinuous1dV1",
    "FORMATION_RETURNS",
    "SKIP_RETURNS",
    "REQUIRED_CLOSES",
    "TOP_N",
    "MAX_ALLOCATION",
    "MAX_SINGLE_WEIGHT",
]
