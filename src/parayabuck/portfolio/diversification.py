from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from parayabuck.logger import get_logger
from parayabuck.models import Bar
from parayabuck.portfolio.correlation import CorrelationMatrix, returns_from_prices
from parayabuck.risk.portfolio import SECTOR_MAP, get_sector

log = get_logger("parayabuck.portfolio.diversification")


@dataclass
class StockCandidate:
    symbol: str
    sector: str
    liquidity_score: float = 0.0
    volatility: float = 0.0
    correlation_penalty: float = 0.0
    final_score: float = 0.0


@dataclass
class DiversificationResult:
    selected: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (symbol, reason)
    sector_counts: dict[str, int] = field(default_factory=dict)


class DiversificationEngine:
    """Selects a diversified basket of stocks from a universe.

    Rules:
      - Max N stocks per sector
      - Min liquidity threshold (avg daily volume)
      - Correlation filter: reject if too correlated with already-selected stocks
      - Volatility preference: lower vol gets priority
    """

    DEFAULT_UNIVERSE = list(SECTOR_MAP.keys())

    def __init__(
        self,
        max_per_sector: int = 3,
        max_total: int = 15,
        min_avg_volume: int = 1_000_000,
        max_correlation: float = 0.7,
        correlation_lookback: int = 60,
        vol_lookback: int = 20,
    ) -> None:
        self.max_per_sector = max_per_sector
        self.max_total = max_total
        self.min_avg_volume = min_avg_volume
        self.max_correlation = max_correlation
        self.correlation_lookback = correlation_lookback
        self.vol_lookback = vol_lookback
        self._correlation = CorrelationMatrix(lookback=correlation_lookback)

    def update_correlations(self, bars_by_symbol: dict[str, list[Bar]]) -> None:
        self._correlation.update(bars_by_symbol)

    def select(
        self,
        candidates: Sequence[str] | None = None,
        bars_by_symbol: dict[str, list[Bar]] | None = None,
    ) -> DiversificationResult:
        universe = list(candidates) if candidates else list(self.DEFAULT_UNIVERSE)
        bars_by_symbol = bars_by_symbol or {}

        if bars_by_symbol:
            self._correlation.update(bars_by_symbol)

        scored: list[StockCandidate] = []
        for symbol in universe:
            sector = get_sector(symbol)
            bars = bars_by_symbol.get(symbol, [])

            vol = self._calc_volatility(bars)
            liq = self._calc_liquidity(bars)

            if liq < self.min_avg_volume and bars:
                log.debug(f"{symbol}: low liquidity ({liq:.0f} < {self.min_avg_volume})")

            scored.append(
                StockCandidate(
                    symbol=symbol,
                    sector=sector,
                    liquidity_score=liq,
                    volatility=vol,
                )
            )

        scored.sort(
            key=lambda c: (
                -c.liquidity_score,
                c.volatility if c.volatility > 0 else 999,
            )
        )

        result = DiversificationResult()

        for candidate in scored:
            if len(result.selected) >= self.max_total:
                break

            sector_count = result.sector_counts.get(candidate.sector, 0)
            if sector_count >= self.max_per_sector:
                result.rejected.append(
                    (candidate.symbol, f"sector '{candidate.sector}' full ({sector_count})")
                )
                continue

            if candidate.volatility > 0 and candidate.volatility > 0.05:
                result.rejected.append(
                    (candidate.symbol, f"high volatility ({candidate.volatility:.2%})")
                )
                continue

            bars = bars_by_symbol.get(candidate.symbol, [])
            if bars:
                cand_returns = returns_from_prices([b.close for b in bars])
                too_correlated = False
                correlated_with = ""
                corr = 0.0
                for sel_sym in result.selected:
                    sel_bars = bars_by_symbol.get(sel_sym, [])
                    if not sel_bars:
                        continue
                    sel_returns = returns_from_prices([b.close for b in sel_bars])
                    corr = self._correlation.calc_correlation(
                        candidate.symbol, cand_returns, sel_returns
                    )
                    if corr > self.max_correlation:
                        too_correlated = True
                        correlated_with = sel_sym
                        break
                if too_correlated:
                    result.rejected.append(
                        (candidate.symbol, f"correlated {corr:.2f} with {correlated_with}")
                    )
                    continue

            result.selected.append(candidate.symbol)
            result.sector_counts[candidate.sector] = sector_count + 1

        log.info(f"Diversification: selected {len(result.selected)} stocks: {result.selected}")
        if result.rejected:
            for sym, reason in result.rejected[:5]:
                log.debug(f"  Rejected {sym}: {reason}")

        return result

    @staticmethod
    def _calc_volatility(bars: list[Bar]) -> float:
        if len(bars) < 2:
            return 0.0
        closes = [b.close for b in bars[-20:]]
        if len(closes) < 2:
            return 0.0
        returns = [abs(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes))]
        return sum(returns) / len(returns) if returns else 0.0

    @staticmethod
    def _calc_liquidity(bars: list[Bar]) -> float:
        if not bars:
            return 0.0
        recent = bars[-20:]
        return sum(b.volume for b in recent) / max(len(recent), 1)

    def get_correlation_report(self) -> str:
        return self._correlation.report()
