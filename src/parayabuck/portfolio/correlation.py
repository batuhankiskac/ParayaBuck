from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from parayabuck.logger import get_logger
from parayabuck.models import Bar

log = get_logger("parayabuck.portfolio.correlation")


def pearson_correlation(x: Sequence[float], y: Sequence[float]) -> float:
    """Pearson correlation coefficient between two series."""
    n = min(len(x), len(y))
    if n < 3:
        return 0.0
    x = list(x[-n:])
    y = list(y[-n:])
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / n
    var_x = sum((xi - mean_x) ** 2 for xi in x) / n
    var_y = sum((yi - mean_y) ** 2 for yi in y) / n
    std_x = var_x**0.5
    std_y = var_y**0.5
    if std_x == 0 or std_y == 0:
        return 0.0
    return float(cov / (std_x * std_y))


def returns_from_prices(prices: Sequence[float]) -> list[float]:
    if len(prices) < 2:
        return []
    return [prices[i] / prices[i - 1] - 1 for i in range(1, len(prices)) if prices[i - 1] != 0]


class CorrelationMatrix:
    """Rolling correlation matrix between all tracked symbols.

    Used to:
      - Detect overly correlated positions
      - Inform diversification decisions
      - Warn when portfolio concentration is high
    """

    def __init__(self, lookback: int = 60) -> None:
        self.lookback = lookback
        self._returns: dict[str, deque[float]] = {}
        self._matrix: dict[str, dict[str, float]] = {}

    def update(self, bars_by_symbol: dict[str, list[Bar]]) -> None:
        # Reset and rebuild from the supplied snapshots so repeated calls don't
        # double-count returns (deque maxlen only trims after overflow, leading
        # to duplicated entries on each update).
        for symbol, bars in bars_by_symbol.items():
            if len(bars) < 2:
                continue
            closes = [b.close for b in bars[-(self.lookback + 1) :]]
            rets = returns_from_prices(closes)
            buf: deque[float] = deque(maxlen=self.lookback)
            for r in rets:
                buf.append(r)
            self._returns[symbol] = buf

        self._compute_matrix()

    def _compute_matrix(self) -> None:
        symbols = list(self._returns.keys())
        self._matrix = {}
        for s1 in symbols:
            self._matrix[s1] = {}
            for s2 in symbols:
                if s1 == s2:
                    self._matrix[s1][s2] = 1.0
                else:
                    r1 = list(self._returns[s1])
                    r2 = list(self._returns[s2])
                    self._matrix[s1][s2] = pearson_correlation(r1, r2)

    def calc_correlation(
        self, symbol_a: str, returns_a: Sequence[float], returns_b: Sequence[float]
    ) -> float:
        return pearson_correlation(returns_a, returns_b)

    def get(self, symbol_a: str, symbol_b: str) -> float:
        return self._matrix.get(symbol_a, {}).get(symbol_b, 0.0)

    def get_matrix(self) -> dict[str, dict[str, float]]:
        return self._matrix

    def avg_correlation(self, symbols: list[str] | None = None) -> float:
        if symbols is None:
            symbols = list(self._matrix.keys())
        if len(symbols) < 2:
            return 0.0
        total = 0.0
        count = 0
        for i, s1 in enumerate(symbols):
            for s2 in symbols[i + 1 :]:
                total += self.get(s1, s2)
                count += 1
        return total / count if count > 0 else 0.0

    def high_correlation_pairs(self, threshold: float = 0.7) -> list[tuple[str, str, float]]:
        pairs: list[tuple[str, str, float]] = []
        symbols = list(self._matrix.keys())
        for i, s1 in enumerate(symbols):
            for s2 in symbols[i + 1 :]:
                corr = self.get(s1, s2)
                if abs(corr) > threshold:
                    pairs.append((s1, s2, corr))
        pairs.sort(key=lambda p: -abs(p[2]))
        return pairs

    def report(self) -> str:
        pairs = self.high_correlation_pairs(0.5)
        if not pairs:
            return "No high correlations detected."
        lines = ["Correlation Report (>0.5):", "-" * 40]
        for s1, s2, corr in pairs[:10]:
            lines.append(f"  {s1:6s} ↔ {s2:6s}: {corr:+.2f}")
        avg = self.avg_correlation()
        lines.append(f"  Average correlation: {avg:.2f}")
        return "\n".join(lines)
