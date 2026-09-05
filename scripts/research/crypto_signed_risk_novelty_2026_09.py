"""Research-only C4 signed-risk score seam; never registered as a product strategy."""

from __future__ import annotations

import dataclasses
import math
from datetime import timedelta
from typing import Sequence

from parayabuck.core.models import Market
from parayabuck.portfolio.base import PortfolioContext
from parayabuck.portfolio.crypto_low_variance_hysteresis import (
    CryptoLowVarianceHysteresis1dV1,
)


IDENTITY = "crypto_signed_risk_22d_hyst4"
VERSION = "0.1.0-research"
LOOKBACK_DAYS = 22
REQUIRED_PRICES = LOOKBACK_DAYS + 1


class CryptoSignedRiskHysteresisResearch(CryptoLowVarianceHysteresis1dV1):
    """C4: replace only the alpha score; inherit frozen T1 portfolio mechanics."""

    name = IDENTITY
    version = VERSION
    display_name = "Crypto Signed Risk 22d with T1 hysteresis (research)"
    market = Market.CRYPTO
    timeframe = "1d"
    supported_timeframes = ("1d",)
    default_rebalance_hours = 96
    rebalance_policy_declared = "hours:96"
    cadence_label = "96h"

    def __init__(self, parameters: dict[str, object] | None = None) -> None:
        super().__init__(parameters)
        self.decision_traces: list[dict[str, object]] = []

    def _score_candidates(self, context: PortfolioContext) -> tuple[tuple[float, str], ...]:
        scored: list[tuple[float, str]] = []
        diagnostics: dict[str, dict[str, object]] = {}
        for symbol in sorted(context.history):
            eligible = context.in_universe(symbol)
            bars = [bar for bar in context.history[symbol] if bar.timestamp <= context.decision_timestamp]
            window = bars[-REQUIRED_PRICES:]
            timestamps_valid = all(
                left.timestamp.tzinfo is not None
                and left.timestamp.utcoffset() == timedelta(0)
                and right.timestamp.tzinfo is not None
                and right.timestamp.utcoffset() == timedelta(0)
                and right.timestamp - left.timestamp == timedelta(days=1)
                for left, right in zip(window, window[1:])
            ) if len(window) == REQUIRED_PRICES else False
            valid = (
                eligible
                and len(bars) >= REQUIRED_PRICES
                and timestamps_valid
                and all(math.isfinite(bar.close) and bar.close > 0 for bar in window)
            )
            reason = "eligible_and_complete" if valid else ("not_in_pit_universe" if not eligible else "insufficient_or_nonconsecutive")
            score = None
            if valid:
                closes = [bar.close for bar in window]
                returns = [right / left - 1.0 for left, right in zip(closes[:-1], closes[1:], strict=True)]
                if all(math.isfinite(value) for value in returns):
                    denominator = sum(value * value for value in returns)
                    if denominator <= 0.0 or not math.isfinite(denominator):
                        reason = "zero_or_invalid_return_energy"
                        diagnostics[symbol] = {"eligible": eligible, "score": None, "reason": reason}
                        continue
                    score = sum(min(value, 0.0) ** 2 for value in returns) / denominator
                    scored.append((score, symbol))
                else:
                    reason = "nonfinite_return"
            diagnostics[symbol] = {"eligible": eligible, "score": score, "reason": reason}
        scored.sort(key=lambda item: (item[0], item[1]))
        rank_map = {symbol: index for index, (_score, symbol) in enumerate(scored, start=1)}
        self._last_score_diagnostics = diagnostics
        self._last_rank_map = rank_map
        return tuple(scored)

    def decide(self, context: PortfolioContext):
        previous_held = sorted(symbol for symbol, weight in context.holdings.items() if weight > 1e-12)
        portfolio = super().decide(context)
        metadata = dict(portfolio.metadata)
        metadata["hypothesis"] = "C4 downside-energy ratio over 22 completed returns; inherited T1 hysteresis rank<=4"
        metadata["score_name"] = "downside_energy_ratio_22d"
        metadata["score_diagnostics"] = getattr(self, "_last_score_diagnostics", {})
        metadata["rank_map"] = getattr(self, "_last_rank_map", {})
        metadata["previous_held"] = tuple(previous_held)
        self.decision_traces.append(
            {
                "decision_timestamp": context.decision_timestamp,
                "candidate_scores": metadata["score_diagnostics"],
                "ranks": metadata["rank_map"],
                "eligible_symbols": sorted(symbol for symbol, row in metadata["score_diagnostics"].items() if row["eligible"]),
                "observed_symbols": sorted(context.history),
                "previous_held": tuple(previous_held),
                "retained": tuple(metadata.get("retained", ())),
                "target_symbols": tuple(sorted(weight.symbol for weight in portfolio.weights)),
                "retention_rank": self.RETENTION_RANK,
                "target_weight": 1.0 / len(portfolio.weights) if portfolio.weights else 0.0,
            }
        )
        return dataclasses.replace(portfolio, metadata=metadata)


def signed_risk_scores(context: PortfolioContext) -> tuple[tuple[float, str], ...]:
    """Pure seam helper for novelty fixtures; uses no future data."""
    return CryptoSignedRiskHysteresisResearch({"top_n": 2, "rebalance_hours": 96})._score_candidates(context)


__all__ = ["CryptoSignedRiskHysteresisResearch", "IDENTITY", "LOOKBACK_DAYS", "REQUIRED_PRICES", "VERSION", "signed_risk_scores"]
