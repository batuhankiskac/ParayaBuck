from __future__ import annotations

from parayabuck.logger import get_logger
from parayabuck.models import Account, Order, Side
from parayabuck.utils import is_crypto

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
    # Sector ETFs (SPDR sector SPDRs).
    "XLK": "technology",
    "XLV": "healthcare",
    "XLE": "energy",
    "XLF": "financial",
    "XLY": "consumer_discretionary",
    "XLP": "consumer_staples",
    "XLI": "industrials",
    "XLB": "materials",
    "XLU": "utilities",
    "XLRE": "real_estate",
    "XLC": "communications",
    # Benchmark / safe-asset proxies.
    "BIL": "cash_proxy",
    "VIXY": "volatility_proxy",
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
        max_daily_loss: float = 0.0,
        max_drawdown: float = 0.0,
        fractional_shares: bool = False,
    ) -> None:
        self.max_pct_per_position = max_pct_per_position
        self.max_pct_per_sector = max_pct_per_sector
        self.max_positions = max_positions
        self.min_cash_pct = min_cash_pct
        # Zero = disabled. When > 0, new BUY orders are rejected once the
        # account's daily PnL or drawdown breaches these USD limits. Exits are
        # always allowed (we never block risk-reducing orders).
        self.max_daily_loss = max_daily_loss
        self.max_drawdown = max_drawdown
        self.fractional_shares = fractional_shares

    def check_order(self, order: Order, account: Account, current_price: float) -> Order | None:
        """Return adjusted order if it passes portfolio risk, None if rejected.

        Exits (closing an existing position) always pass through unchanged —
        we never block risk-reducing orders, even when a kill-switch is tripped
        or a sector/position limit is saturated. Blocking an exit would strand
        risk exactly when we most need to reduce it.
        """
        if account.equity <= 0:
            return None

        # Kill-switches and concentration limits apply only to NEW entries
        # (BUY that opens a long, or SELL that opens a short). An exit (SELL
        # closing a long, BUY closing a short) must always be allowed so the
        # engine can defensively close risk when limits are breached.
        current_pos = account.get_position(order.symbol)
        is_exit = (order.side == Side.SELL and current_pos.is_long) or (
            order.side == Side.BUY and current_pos.is_short
        )

        if is_exit:
            # Exits pass through unchanged — never trim or block them.
            return order

        if self.max_daily_loss > 0 and account.daily_pnl <= -self.max_daily_loss:
            log.warning(
                f"Order rejected: daily loss limit reached "
                f"(daily PnL ${account.daily_pnl:+.2f} <= "
                f"-${self.max_daily_loss:.2f})"
            )
            return None
        drawdown = account.drawdown
        if self.max_drawdown > 0 and drawdown >= self.max_drawdown:
            log.warning(
                f"Order rejected: max drawdown reached "
                f"(${drawdown:.2f} >= ${self.max_drawdown:.2f})"
            )
            return None

        crypto = is_crypto(order.symbol)
        # Crypto trades in fractional quantities; stocks are whole shares.
        fractional = crypto or self.fractional_shares
        min_lot = 0.0001 if fractional else 1

        order_value = order.qty * current_price
        pct_of_equity = order_value / account.equity

        # Check single position limit
        if pct_of_equity > self.max_pct_per_position:
            max_shares = (account.equity * self.max_pct_per_position) / current_price
            if fractional:
                max_shares = round(max_shares, 4)
            else:
                max_shares = int(max_shares)
            if max_shares < min_lot:
                log.warning(
                    f"Order rejected: {order.symbol} would exceed "
                    f"{self.max_pct_per_position:.0%} of equity"
                )
                return None
            from parayabuck.models import Order as OrderCls

            log.info(f"Trimming {order.symbol} from {order.qty} to {max_shares} (position limit)")
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
        if current_pos.is_flat:
            active_positions = sum(1 for p in account.positions.values() if not p.is_flat)
            if active_positions >= self.max_positions:
                log.warning(f"Order rejected: max positions ({self.max_positions}) reached")
                return None

        # Check cash buffer
        if order.side == Side.BUY:
            cash_needed = order.qty * current_price
            available_cash = account.balance * (1 - self.min_cash_pct)
            if cash_needed > available_cash:
                affordable = available_cash / current_price
                if fractional:
                    affordable = round(affordable, 4)
                else:
                    affordable = int(affordable)
                if affordable < min_lot:
                    log.warning(
                        f"Order rejected: insufficient cash (need ${cash_needed:.0f}, "
                        f"available ${available_cash:.0f})"
                    )
                    return None
                from parayabuck.models import Order as OrderCls

                log.info(f"Trimming {order.symbol} to {affordable} (cash buffer)")
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
        """Calculate total sector exposure as fraction of equity (including new order).

        Existing positions are valued at each position's own current/avg price
        rather than the new order's price (which only applies to one symbol).
        """
        if account.equity <= 0:
            return 1.0

        sector_value = 0.0
        for sym, pos in account.positions.items():
            if pos.is_flat:
                continue
            if get_sector(sym) == sector:
                # Use the order's price for the symbol being traded; for others
                # fall back to avg cost (best available proxy without live quotes).
                price = (
                    current_price
                    if sym == new_order.symbol
                    else (pos.current_price if pos.current_price is not None else pos.avg_price)
                )
                sector_value += abs(pos.qty) * price

        # Both BUY (new long) and SELL (new short) add gross exposure.
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
