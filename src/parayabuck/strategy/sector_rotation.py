from __future__ import annotations

from collections import deque
from typing import Any

from parayabuck.logger import get_logger
from parayabuck.models import Bar, Order, OrderType, Position, Side
from parayabuck.strategy.base import Strategy

log = get_logger("parayabuck.strategy.sector_rotation")


# Default sector ETF universe. Each entry maps a sector ETF to its sector.
# These are the SPDR sector ETFs — liquid, tradeable on Alpaca, and the
# canonical set used by every sector-rotation study since 1998.
DEFAULT_SECTOR_ETFS: list[str] = [
    "XLK",   # technology
    "XLV",   # healthcare
    "XLF",   # financials
    "XLY",   # consumer discretionary
    "XLP",   # consumer staples
    "XLE",   # energy
    "XLI",   # industrials
    "XLB",   # materials
    "XLU",   # utilities
    "XLRE",  # real estate
    "XLC",   # communication services
]


class SectorRotation(Strategy):
    """Sector Rotation strategy — relative-strength among sector ETFs.

    Ranks the configured sector ETFs by their lookback-window return, then
    holds the top `top_n` ETFs (equal-weighted). Rebalances every
    `rebalance_bars` bars. The strategy is a "buy the winners" rotation:
    sectors that have led over the past N bars tend to continue leading for
    a few more weeks (industry momentum is one of the most robust equity
    anomalies).

    Signal:
      - Every `rebalance_bars` bars, compute each sector ETF's return over
        `lookback` bars.
      - Buy the top `top_n` ETFs (equal weight); flatten any ETF that fell
        out of the top `top_n`.
      - If an ETF's return is negative, skip it (cash is better than a
        losing sector — same absolute-momentum filter as Dual Momentum).

    Multi-symbol: this strategy consumes ALL configured sector ETFs. It uses
    `self.symbols` to declare them and queues orders via `self._pending_orders`
    for the secondary legs.

    Params:
        sector_etfs: List of sector ETF tickers (default: the 11 SPDR sector
            ETFs).
        lookback: Return lookback in bars (default 63 ~ 3 months on daily).
        top_n: Number of ETFs to hold (default 3).
        rebalance_bars: Re-evaluate ranking every N bars (default 21 ~
            monthly on daily). Lower = more churn.
        bars_per_day: Bars per day for the running timeframe. When set,
            `lookback` and `rebalance_bars` are rescaled to calendar time.
        max_position: Max shares per ETF (default 100).
    """

    name = "sector_rotation"

    def __init__(self, **params: Any) -> None:
        super().__init__(
            sector_etfs=params.get("sector_etfs", DEFAULT_SECTOR_ETFS),
            lookback=params.get("lookback", 63),
            top_n=params.get("top_n", 3),
            rebalance_bars=params.get("rebalance_bars", 21),
            bars_per_day=params.get("bars_per_day"),
            max_position=params.get("max_position", 100),
        )
        self.sector_etfs = list(self.params["sector_etfs"])
        self.symbols = list(self.sector_etfs)
        # Rescale for intraday timeframes.
        daily_bpd = 252.0
        bpd = float(self.params.get("bars_per_day") or daily_bpd)
        self.bars_per_day = bpd
        scale = bpd / daily_bpd if daily_bpd > 0 else 1.0
        self.lookback = max(int(round(int(self.params["lookback"]) * scale)), 10)
        self.rebalance_bars = max(int(round(int(self.params["rebalance_bars"]) * scale)), 1)
        self.top_n = max(1, int(self.params["top_n"]))
        self.max_position = int(self.params["max_position"])

        buf_len = max(self.lookback * 2, 400)
        self._bars_by_symbol: dict[str, deque[Bar]] = {
            sym: deque(maxlen=buf_len) for sym in self.sector_etfs
        }
        self._bars_since_rebalance: int = 0
        # Currently-held symbols (set after each rebalance). Used to decide
        # which ETFs to flatten on the next rebalance.
        self._holding: set[str] = set()

    def _returns(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for sym, bars in self._bars_by_symbol.items():
            if len(bars) < self.lookback + 1:
                continue
            closes = [b.close for b in bars]
            past = closes[-self.lookback - 1]
            now = closes[-1]
            if past > 0:
                out[sym] = (now - past) / past
        return out

    def on_bar(self, bar: Bar, position: Position) -> Order | None:
        self._bars_seen += 1
        if bar.symbol not in self._bars_by_symbol:
            return None
        self._bars_by_symbol[bar.symbol].append(bar)

        # We only act on rebalance bars, and only when the bar we just received
        # is for one of the symbols we hold or want to evaluate. Since engines
        # feed bars in timestamp order across symbols, we trigger rebalance on
        # the bar of the first symbol we see after `rebalance_bars` elapsed.
        self._bars_since_rebalance += 1
        if self._bars_since_rebalance < self.rebalance_bars:
            return None
        self._bars_since_rebalance = 0

        returns = self._returns()
        if not returns:
            return None

        # Rank by return descending; drop negative-return ETFs (absolute
        # momentum filter — don't hold losing sectors, even if they're "top").
        ranked = sorted(returns.items(), key=lambda x: -x[1])
        candidates = [(sym, ret) for sym, ret in ranked if ret > 0]
        target = set(sym for sym, _ in candidates[: self.top_n])

        # Symbols to exit: were holding, no longer in target.
        to_exit = self._holding - target
        # Symbols to enter: in target, not currently holding.
        to_enter = target - self._holding

        if not to_exit and not to_enter:
            return None

        # Emit orders: exits first (free up capital), then entries. The first
        # order is returned, the rest queued.
        orders: list[Order] = []
        for sym in to_exit:
            orders.append(
                Order(
                    symbol=sym,
                    side=Side.SELL,
                    qty=self.max_position,  # engine will trim to actual position
                    order_type=OrderType.MARKET,
                )
            )
        for sym in to_enter:
            orders.append(
                Order(
                    symbol=sym,
                    side=Side.BUY,
                    qty=min(self.max_position, 1),
                    order_type=OrderType.MARKET,
                )
            )

        self._holding = target
        if not orders:
            return None
        # Queue all but the first for the engine to drain.
        for od in orders[1:]:
            self._pending_orders.append(od)
        log.info(
            f"SECTOR ROTATION rebalance: hold={sorted(target)} "
            f"exit={sorted(to_exit)} enter={sorted(to_enter)}"
        )
        return orders[0]
