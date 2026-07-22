from __future__ import annotations

from parayabuck.core.models import Signal


class SignalRanker:
    def __init__(self, minimum_score: float = 65.0) -> None:
        self.minimum_score = minimum_score

    def rank(self, signals: list[Signal]) -> list[Signal]:
        """Keep the best setup per symbol and rank by quality, confidence and R:R."""
        best_by_symbol: dict[str, Signal] = {}
        for signal in signals:
            if signal.score < self.minimum_score:
                continue
            current = best_by_symbol.get(signal.symbol)
            quality = signal.score * signal.confidence * min(signal.risk_reward, 3.0)
            current_quality = (
                current.score * current.confidence * min(current.risk_reward, 3.0)
                if current is not None
                else -1.0
            )
            if quality > current_quality:
                best_by_symbol[signal.symbol] = signal
        return sorted(
            best_by_symbol.values(),
            key=lambda item: (item.score * item.confidence, item.risk_reward),
            reverse=True,
        )
