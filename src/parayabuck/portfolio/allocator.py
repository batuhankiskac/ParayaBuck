from __future__ import annotations

from dataclasses import dataclass, field

from parayabuck.logger import get_logger

log = get_logger("parayabuck.portfolio.allocator")


@dataclass
class StrategyAllocation:
    strategy_name: str
    capital_fraction: float
    current_pnl: float = 0.0
    sharpe: float = 0.0
    trades: int = 0
    weight: float = 0.0


@dataclass
class StrategyPerformance:
    name: str
    total_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    equity_curve: list[float] = field(default_factory=list)

    @property
    def total_trades(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        return self.wins / self.total_trades if self.total_trades > 0 else 0.0

    @property
    def profit_factor(self) -> float:
        return self.gross_profit / self.gross_loss if self.gross_loss > 0 else 0.0

    @property
    def sharpe(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        returns = [
            self.equity_curve[i] / self.equity_curve[i - 1] - 1
            for i in range(1, len(self.equity_curve))
            if self.equity_curve[i - 1] != 0
        ]
        if not returns:
            return 0.0
        mean_r = sum(returns) / len(returns)
        var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        std_r = var_r**0.5
        if std_r == 0:
            return 0.0
        return float((mean_r / std_r) * (252**0.5))


class StrategyAllocator:
    """Allocates capital across multiple strategies based on performance.

    Dynamic allocation:
      - Each strategy gets a base allocation
      - Performance adjustment: higher Sharpe → more capital
      - Drawdown penalty: losing strategies get less
      - Min/max bounds to prevent concentration
    """

    def __init__(
        self,
        allocations: dict[str, float],
        min_fraction: float = 0.05,
        max_fraction: float = 0.50,
        rebalance_threshold: float = 0.10,
        performance_weight: float = 0.3,
    ) -> None:
        total = sum(allocations.values())
        if total <= 0:
            raise ValueError("Total allocation must be positive")
        self.base_allocations = {k: v / total for k, v in allocations.items()}
        self.min_fraction = min_fraction
        self.max_fraction = max_fraction
        self.rebalance_threshold = rebalance_threshold
        self.performance_weight = performance_weight
        self._current_allocations = dict(self.base_allocations)
        self._performances: dict[str, StrategyPerformance] = {
            name: StrategyPerformance(name=name) for name in allocations
        }

    def record_trade(self, strategy_name: str, pnl: float) -> None:
        perf = self._performances.setdefault(strategy_name, StrategyPerformance(name=strategy_name))
        if pnl > 0:
            perf.wins += 1
            perf.gross_profit += pnl
        else:
            perf.losses += 1
            perf.gross_loss += abs(pnl)
        perf.total_pnl += pnl

    def update_equity(self, strategy_name: str, equity: float) -> None:
        perf = self._performances.setdefault(strategy_name, StrategyPerformance(name=strategy_name))
        perf.equity_curve.append(equity)

    def get_allocation(self, strategy_name: str) -> float:
        return self._current_allocations.get(strategy_name, 0.0)

    def get_capital(self, strategy_name: str, total_capital: float) -> float:
        return total_capital * self.get_allocation(strategy_name)

    def rebalance(self) -> dict[str, float]:
        """Recalculate allocations based on performance. Returns new weights."""
        sharpes = {name: p.sharpe for name, p in self._performances.items()}
        total_sharpe = sum(max(s, 0) for s in sharpes.values())

        new_allocs: dict[str, float] = {}
        for name, base_frac in self.base_allocations.items():
            perf_adj = 0.0
            if total_sharpe > 0:
                perf_adj = (max(sharpes.get(name, 0), 0) / total_sharpe) * self.performance_weight
            static_frac = base_frac * (1 - self.performance_weight)
            new_frac = static_frac + perf_adj

            if self._performances[name].total_pnl < 0:
                drawdown_penalty = min(0.5, abs(self._performances[name].total_pnl) / 1000)
                new_frac *= 1 - drawdown_penalty

            new_allocs[name] = new_frac

        total = sum(new_allocs.values())
        if total > 0:
            new_allocs = {k: v / total for k, v in new_allocs.items()}

        for name in new_allocs:
            new_allocs[name] = max(self.min_fraction, min(self.max_fraction, new_allocs[name]))

        total = sum(new_allocs.values())
        if total > 0:
            new_allocs = {k: v / total for k, v in new_allocs.items()}

        self._current_allocations = new_allocs

        for name, frac in new_allocs.items():
            log.info(
                f"Allocation {name}: {frac:.1%} "
                f"(Sharpe={sharpes.get(name, 0):.2f}, "
                f"PnL={self._performances[name].total_pnl:+.2f})"
            )

        return new_allocs

    def needs_rebalance(self) -> bool:
        for name, target in self._current_allocations.items():
            base = self.base_allocations.get(name, 0)
            if base > 0 and abs(target - base) / base > self.rebalance_threshold:
                return True
        return False

    def summary(self) -> str:
        lines = ["Strategy Allocation Summary:", "-" * 50]
        for name, frac in self._current_allocations.items():
            perf = self._performances.get(name)
            pnl = perf.total_pnl if perf else 0
            sharpe = perf.sharpe if perf else 0
            lines.append(f"  {name:20s} {frac:6.1%}  PnL=${pnl:+8.2f}  Sharpe={sharpe:.2f}")
        return "\n".join(lines)

    def get_performances(self) -> dict[str, StrategyPerformance]:
        return self._performances
