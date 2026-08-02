from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from parayabuck.core.models import Direction, Market

EXIT_STOP = "stop"
EXIT_TARGET = "target"
EXIT_END_OF_TEST = "end"


@dataclass(frozen=True)
class TradeRecord:
    """A fully closed round-trip recorded by the backtest broker.

    MAE/MFE excursions are percentages of the entry price and only count bars
    after the entry bar (intrabar order within the entry bar is unknown).
    """

    client_order_id: str
    symbol: str
    market: Market
    strategy: str
    direction: Direction
    qty: float
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    pnl: float
    return_pct: float
    mae_pct: float
    mfe_pct: float
    exit_reason: str
    fees: float

    @property
    def holding_days(self) -> float:
        return (self.exit_time - self.entry_time).total_seconds() / 86_400

    def to_dict(self) -> dict[str, object]:
        return {
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "market": self.market.value,
            "strategy": self.strategy,
            "direction": self.direction.value,
            "qty": self.qty,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat(),
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "pnl": self.pnl,
            "return_pct": self.return_pct,
            "mae_pct": self.mae_pct,
            "mfe_pct": self.mfe_pct,
            "exit_reason": self.exit_reason,
            "holding_days": self.holding_days,
            "fees": self.fees,
        }


__all__ = ["EXIT_END_OF_TEST", "EXIT_STOP", "EXIT_TARGET", "TradeRecord"]
