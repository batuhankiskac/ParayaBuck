from __future__ import annotations

from parayabuck.logger import get_logger
from parayabuck.models import Account, Order, Side

log = get_logger("parayabuck.risk.portfolio")


SECTOR_MAP: dict[str, str] = {
    "AAPL": "technology",
    "MSFT": "technology",
    "GOOGL": "technology",
    "NVDA": "technology",
    "AMZN": "consumer",
    "TSLA": "consumer",
    "META": "technology",
    "SPY": "index",
    "QQQ": "index",
    "JNJ": "healthcare",
    "UNH": "healthcare",
    "PFE": "healthcare",
    "JPM": "financial",
    "BAC": "financial",
    "GS": "financial",
    "KO": "consumer",
    "PEP": "consumer",
    "PG": "consumer",
    "WMT": "consumer",
    "XOM": "energy",
    "CVX": "energy",
    "XLF": "sector_etf",
    "XLV": "sector_etf",
    "XLE": "sector_etf",
    # Crypto pairs (7/24, fractional qty)
    "BTC/USD": "crypto",
    "ETH/USD": "crypto",
    "SOL/USD": "crypto",
    "ADA/USD": "crypto",
    "XRP/USD": "crypto",
    "DOGE/USD": "crypto",
    "AVAX/USD": "crypto",
    "LINK/USD": "crypto",
    "LTC/USD": "crypto",
    "MATIC/USD": "crypto",
}


def get_sector(symbol: str) -> str:
    if "/" in symbol:
        return "crypto"
    return SECTOR_MAP.get(symbol, "other")


class PortfolioRiskManager:
    """Portfolio-level risk checks: diversification, sector, correlation limits.

    Rules enforced:
      - Max % of equity per single position
      - Max % of equity per sector
      - Max number of concurrent positions
      - Min cash buffer (always keep some cash)
    """

    def __init__(
        self,
        max_pct_per_position: float = 0.10,
        max_pct_per_sector: float = 0.30,
        max_positions: int = 15,
        min_cash_pct: float = 0.05,
    ) -> None:
        self.max_pct_per_position = max_pct_per_position
        self.max_pct_per_sector = max_pct_per_sector
        self.max_positions = max_positions
        self.min_cash_pct = min_cash_pct

    def check_order(self, order: Order, account: Account, current_price: float) -> Order | None:
        """Return adjusted order if it passes portfolio risk, None if rejected."""
        if account.equity <= 0:
            return None

        order_value = order.qty * current_price
        pct_of_equity = order_value / account.equity

        # Check single position limit
        if pct_of_equity > self.max_pct_per_position:
            max_shares = int(
                (account.equity * self.max_pct_per_position) / current_price
            )
            if max_shares <= 0:
                log.warning(
                    f"Order rejected: {order.symbol} would exceed "
                    f"{self.max_pct_per_position:.0%} of equity"
                )
                return None
            from parayabuck.models import Order as OrderCls
            log.info(
                f"Trimming {order.symbol} from {order.qty} to {max_shares} shares "
                f"(position limit)"
            )
            order = OrderCls(
                symbol=order.symbol,
                side=order.side,
                qty=max_shares,
                order_type=order.order_type,
                limit_price=order.limit_price,
                stop_price=order.stop_price,
                tif=order.tif,
            )

        # Check sector concentration
        sector = get_sector(order.symbol)
        sector_exposure = self._sector_exposure(account, sector, order, current_price)
        if sector_exposure > self.max_pct_per_sector:
            log.warning(
                f"Order rejected: {order.symbol} sector '{sector}' would be "
                f"{sector_exposure:.0%} of equity (max {self.max_pct_per_sector:.0%})"
            )
            return None

        # Check max number of positions (only for new positions)
        if order.side == Side.BUY:
            current_pos = account.get_position(order.symbol)
            if current_pos.is_flat:
                active_positions = sum(
                    1 for p in account.positions.values() if not p.is_flat
                )
                if active_positions >= self.max_positions:
                    log.warning(
                        f"Order rejected: max positions ({self.max_positions}) reached"
                    )
                    return None

        # Check cash buffer
        if order.side == Side.BUY:
            cash_needed = order.qty * current_price
            available_cash = account.balance * (1 - self.min_cash_pct)
            if cash_needed > available_cash:
                affordable = int(available_cash / current_price)
                if affordable <= 0:
                    log.warning(
                        f"Order rejected: insufficient cash (need ${cash_needed:.0f}, "
                        f"available ${available_cash:.0f})"
                    )
                    return None
                from parayabuck.models import Order as OrderCls
                log.info(
                    f"Trimming {order.symbol} to {affordable} shares (cash buffer)"
                )
                order = OrderCls(
                    symbol=order.symbol,
                    side=order.side,
                    qty=affordable,
                    order_type=order.order_type,
                    limit_price=order.limit_price,
                    stop_price=order.stop_price,
                    tif=order.tif,
                )

        return order

    def _sector_exposure(
        self, account: Account, sector: str, new_order: Order, current_price: float
    ) -> float:
        """Calculate total sector exposure as fraction of equity (including new order)."""
        if account.equity <= 0:
            return 1.0

        sector_value = 0.0
        for sym, pos in account.positions.items():
            if pos.is_flat:
                continue
            if get_sector(sym) == sector:
                sector_value += abs(pos.qty) * current_price

        if new_order.side == Side.BUY:
            sector_value += new_order.qty * current_price

        return sector_value / account.equity

    def position_exposure(self, account: Account, symbol: str, current_price: float) -> float:
        """Current exposure of a single position as % of equity."""
        if account.equity <= 0:
            return 0.0
        pos = account.get_position(symbol)
        if pos.is_flat:
            return 0.0
        return abs(pos.qty) * current_price / account.equity

    def total_exposure(self, account: Account, prices: dict[str, float]) -> float:
        """Total long exposure as % of equity."""
        if account.equity <= 0:
            return 0.0
        total = 0.0
        for sym, pos in account.positions.items():
            if pos.is_flat:
                continue
            price = prices.get(sym, pos.avg_price)
            total += abs(pos.qty) * price
        return total / account.equity
