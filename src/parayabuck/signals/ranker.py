from __future__ import annotations

from parayabuck.core.models import Regime, Signal
from parayabuck.logger import get_logger
from parayabuck.markets.symbols import canonical_symbol

log = get_logger("parayabuck.signals.ranker")


def ranking_score(signal: Signal) -> float:
    if signal.normalized_expected_r is not None:
        return signal.normalized_expected_r
    normalized_setup_score = signal.score / 100.0
    rr_modifier = min(max(signal.risk_reward, 0.0), 3.0) / 3.0
    regime_modifier = {
        Regime.HIGH_VOLATILITY: 0.9,
        Regime.UNKNOWN: 0.85,
    }.get(signal.regime, 1.0)
    return normalized_setup_score * rr_modifier * regime_modifier


class SignalRanker:
    def __init__(self, minimum_score: float = 65.0) -> None:
        self.minimum_score = minimum_score

    def rank(self, signals: list[Signal]) -> list[Signal]:
        """Keep the best setup per symbol and rank by quality, confidence and R:R."""
        best_by_symbol: dict[str, Signal] = {}
        for signal in signals:
            if signal.score < self.minimum_score:
                continue
            try:
                key = canonical_symbol(signal.symbol, signal.market)
            except ValueError:
                # One malformed symbol must not abort the portfolio-wide
                # decision cycle; skip it and let the cycle continue.
                log.warning(
                    "Skipping signal with malformed symbol %r (market=%s)",
                    signal.symbol,
                    signal.market.value,
                )
                continue
            current = best_by_symbol.get(key)
            quality = ranking_score(signal)
            current_quality = ranking_score(current) if current is not None else -1.0
            if quality > current_quality:
                best_by_symbol[key] = signal
        return sorted(
            best_by_symbol.values(),
            key=ranking_score,
            reverse=True,
        )


__all__ = ["SignalRanker", "ranking_score"]
