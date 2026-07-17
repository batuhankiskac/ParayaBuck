from __future__ import annotations

from parayabuck.config import Config
from parayabuck.logger import get_logger
from parayabuck.models import Account, Order

log = get_logger("parayabuck.risk.manager")


class RiskManager:
    """Checks every order against risk rules before it's sent.

    Rules enforced:
      - Max position size (shares per trade)
      - Max daily loss (stop trading if daily PnL <= -max_daily_loss)
      - Max drawdown (stop trading if drawdown >= max_drawdown)
      - Position sizing based on risk_per_trade (optional)
      - Cooldown after halt (N bars before resuming)
    """

    def __init__(self, config: Config) -> None:
        self.risk_cfg = config.risk
        self.symbols = config.symbols
        self._trading_halted = False
        self._halt_reason: str | None = None
        self._cooldown_bars = 0
        self._cooldown_remaining = 0

    @property
    def is_halted(self) -> bool:
        return self._trading_halted

    @property
    def halt_reason(self) -> str | None:
        return self._halt_reason

    def set_cooldown(self, bars: int) -> None:
        """Set a cooldown period (in bars) after a halt or loss."""
        self._cooldown_bars = bars
        self._cooldown_remaining = bars

    def tick_cooldown(self) -> None:
        """Decrement cooldown by one bar."""
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            if self._cooldown_remaining == 0:
                log.info("Cooldown ended, resuming trading")
                self._trading_halted = False
                self._halt_reason = None

    def check_account(self, account: Account) -> None:
        """Update halt status based on current account state."""
        if self._trading_halted:
            return

        if account.daily_pnl <= -self.risk_cfg.max_daily_loss:
            self._trading_halted = True
            self._halt_reason = (
                f"Daily loss limit reached: ${account.daily_pnl:.2f} "
                f"<= -${self.risk_cfg.max_daily_loss:.2f}"
            )
            self._cooldown_remaining = self._cooldown_bars
            log.warning(f"TRADING HALTED: {self._halt_reason}")
            return

        if account.drawdown >= self.risk_cfg.max_drawdown:
            self._trading_halted = True
            self._halt_reason = (
                f"Max drawdown reached: ${account.drawdown:.2f} "
                f">= ${self.risk_cfg.max_drawdown:.2f}"
            )
            self._cooldown_remaining = self._cooldown_bars
            log.warning(f"TRADING HALTED: {self._halt_reason}")
            return

    def check_order(self, order: Order, account: Account) -> Order | None:
        """Return the order if it passes risk checks, None if rejected."""
        if self._trading_halted:
            return None

        # Check max position size
        if order.qty > self.risk_cfg.max_position_size:
            order = Order(
                symbol=order.symbol,
                side=order.side,
                qty=self.risk_cfg.max_position_size,
                order_type=order.order_type,
                limit_price=order.limit_price,
                stop_price=order.stop_price,
                tif=order.tif,
            )

        # Check account risk
        self.check_account(account)
        if self._trading_halted:
            return None

        return order

    def calc_commission(self, qty: float) -> float:
        return abs(qty) * self.risk_cfg.commission_per_share

    def can_trade(self) -> bool:
        """Quick check: is trading currently allowed?"""
        return not self._trading_halted and self._cooldown_remaining == 0
