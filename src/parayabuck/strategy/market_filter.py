from __future__ import annotations

from dataclasses import dataclass

from parayabuck.logger import get_logger
from parayabuck.models import Bar
from parayabuck.strategy.indicators import sma

log = get_logger("parayabuck.strategy.market_filter")


@dataclass
class MarketFilterState:
    """Snapshot of the global market filter.

    Attributes:
        spy_above_200sma: True if SPY (or the configured benchmark) is above
            its 200-period SMA — the classic "risk-on" filter. When False,
            new long entries should be blocked market-wide.
        vix_high: True if the VIX proxy is above `vix_high_threshold`.
        vix_value: The latest VIX-proxy value (or 0.0 if unavailable).
        allowed: True if new long entries are permitted market-wide.
        reason: Human-readable explanation when `allowed` is False.
    """

    spy_above_200sma: bool
    vix_high: bool
    vix_value: float
    allowed: bool
    reason: str = ""

    def __repr__(self) -> str:
        return (
            f"MarketFilterState(spy_above_200sma={self.spy_above_200sma}, "
            f"vix_high={self.vix_high}, vix={self.vix_value:.2f}, "
            f"allowed={self.allowed}, reason={self.reason!r})"
        )


class GlobalMarketFilter:
    """Market-wide risk filter: SPY 200-SMA + VIX proxy.

    Most equity strategies are long-biased and bleed in broad market down-
    trends. A simple, robust filter that has worked for decades:

      - If SPY is below its 200-day SMA -> bearish regime; no new longs.
      - If VIX > 30 -> panic regime; no new longs (and reduce size).

    The VIX index itself is not directly available on Alpaca, so we accept a
    *proxy* symbol — `VIXY` (ProShares VIX Short-Term Futures ETF) by default.
    The proxy's *price level* is not the VIX, but its SMA-ratio (current /
    long-SMA) tracks VIX spikes well: when VIX shoots to 30+, VIXY roughly
    doubles off its base. We trigger `vix_high` when the proxy is above
    `vix_high_ratio` x its long SMA (default 1.5 — a 50% spike).

    This single filter catches the vast majority of crisis regimes the per-
    symbol ATR/volatility ratio misses, because volatility-index futures
    front-run broad-market selling before single-name price action confirms.
    """

    def __init__(
        self,
        benchmark_symbol: str = "SPY",
        benchmark_sma_period: int = 200,
        vix_proxy_symbol: str = "VIXY",
        vix_proxy_sma_period: int = 50,
        vix_high_ratio: float = 1.5,
        enabled: bool = True,
    ) -> None:
        self.benchmark_symbol = benchmark_symbol
        self.benchmark_sma_period = benchmark_sma_period
        self.vix_proxy_symbol = vix_proxy_symbol
        self.vix_proxy_sma_period = vix_proxy_sma_period
        self.vix_high_ratio = vix_high_ratio
        self.enabled = enabled

    def evaluate(
        self,
        benchmark_bars: list[Bar] | None,
        vix_bars: list[Bar] | None,
    ) -> MarketFilterState:
        """Compute the current market filter state.

        Args:
            benchmark_bars: historical bars for SPY (or the configured
                benchmark). Must be at least `benchmark_sma_period` long to
                produce a meaningful SMA; shorter inputs fail closed so an
                unavailable benchmark cannot silently enable new equity risk.
            vix_bars: historical bars for the VIX proxy. Optional — if None
                or too short, only the SPY filter applies.
        """
        if not self.enabled:
            return MarketFilterState(
                spy_above_200sma=True,
                vix_high=False,
                vix_value=0.0,
                allowed=True,
                reason="filter disabled",
            )

        # SPY 200 SMA filter
        spy_above = False
        spy_reason = ""
        if benchmark_bars and len(benchmark_bars) >= self.benchmark_sma_period:
            closes = [b.close for b in benchmark_bars]
            sma_val = sma(closes, self.benchmark_sma_period)
            if sma_val is not None:
                spy_above = closes[-1] > sma_val
                if not spy_above:
                    spy_reason = (
                        f"{self.benchmark_symbol} below {self.benchmark_sma_period}-SMA "
                        f"({closes[-1]:.2f} < {sma_val:.2f})"
                    )
        else:
            received = len(benchmark_bars) if benchmark_bars else 0
            spy_reason = (
                f"insufficient {self.benchmark_symbol} history "
                f"({received}/{self.benchmark_sma_period} bars)"
            )

        # VIX proxy filter
        vix_high = False
        vix_value = 0.0
        vix_reason = ""
        if vix_bars and len(vix_bars) >= self.vix_proxy_sma_period:
            vix_closes = [b.close for b in vix_bars]
            vix_value = vix_closes[-1]
            vix_sma = sma(vix_closes, self.vix_proxy_sma_period)
            if vix_sma is not None and vix_sma > 0:
                ratio = vix_value / vix_sma
                if ratio >= self.vix_high_ratio:
                    vix_high = True
                    vix_reason = (
                        f"VIX proxy {self.vix_proxy_symbol} spiked "
                        f"({vix_value:.2f} / {vix_sma:.2f} = {ratio:.2f}x "
                        f">= {self.vix_high_ratio}x)"
                    )

        allowed = spy_above and not vix_high
        reasons = [r for r in (spy_reason, vix_reason) if r]
        return MarketFilterState(
            spy_above_200sma=spy_above,
            vix_high=vix_high,
            vix_value=vix_value,
            allowed=allowed,
            reason="; ".join(reasons) if reasons else "ok",
        )


__all__ = ["GlobalMarketFilter", "MarketFilterState"]
