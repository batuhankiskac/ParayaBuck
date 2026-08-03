from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from parayabuck.core.models import Direction, Market, Regime

EXIT_STOP = "stop"
EXIT_TARGET = "target"
EXIT_END_OF_TEST = "end"
EXIT_BREAK_EVEN = "break_even"
EXIT_TRAILING_STOP = "trailing_stop"
EXIT_TIME = "time"


@dataclass(frozen=True)
class TradeRecord:
    """A fully closed round-trip recorded by the backtest broker.

    MAE/MFE excursions are percentages of the actual entry fill. The entry bar
    is included because the next-open fill is known to precede that bar's
    subsequent high/low range; ambiguous stop/target ordering is still handled
    by the broker's configured conservative intrabar policy.
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
    strategy_version: str = "1.0.0"
    exit_policy_id: str = "fixed"
    initial_risk: float = 0.0
    bars_held: int = 0
    entry_score: float = 0.0
    normalized_expected_r: float | None = None
    entry_regime: Regime = Regime.UNKNOWN

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
            "strategy_version": self.strategy_version,
            "exit_policy_id": self.exit_policy_id,
            "initial_risk": self.initial_risk,
            "bars_held": self.bars_held,
            "entry_score": self.entry_score,
            "normalized_expected_r": self.normalized_expected_r,
            "entry_regime": self.entry_regime.value,
        }


__all__ = [
    "EXIT_BREAK_EVEN",
    "EXIT_END_OF_TEST",
    "EXIT_STOP",
    "EXIT_TARGET",
    "EXIT_TIME",
    "EXIT_TRAILING_STOP",
    "TradeRecord",
]
